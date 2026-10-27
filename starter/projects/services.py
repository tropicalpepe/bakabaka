"""Project domain services. All mutations authorise via authorize_project first."""
import json
import logging

from django.db import transaction

from core.errors import Conflict, Forbidden, NotFound
from core.utils import make_uuid

logger = logging.getLogger(__name__)


def authorize_project(actor, project_id, action="view"):
    """Load project and verify actor permission. Raises NotFound or Forbidden."""
    from .models import Project

    try:
        project = Project.objects.select_related("owner").get(pk=project_id)
    except Project.DoesNotExist:
        raise NotFound("project", project_id)

    if not actor.is_authenticated or not actor.is_active:
        raise Forbidden(action)

    if actor.role == "student" and project.owner_id == actor.id:
        return project

    if actor.role == "supervisor":
        from supervision.models import SupervisorAssignment
        accepted = SupervisorAssignment.objects.filter(
            project_id=project_id,
            supervisor_id=actor.id,
            state="accepted",
        ).exists()
        if accepted:
            return project

    if actor.role == "admin":
        return project

    raise Forbidden(action)


def save_context(actor, project_id, title, idea, keywords, expected_revision, programme=None):
    """Update project context with optimistic locking. Raises Conflict on stale revision."""
    with transaction.atomic():
        project = authorize_project(actor, project_id, "edit_context")
        if project.context_revision != expected_revision:
            raise Conflict("stale_revision")
        project.title = title
        project.idea = idea
        project.keywords = list(keywords)
        if programme:
            # Update student profile programme if available
            try:
                project.owner.student_profile.programme = programme
                project.owner.student_profile.save(update_fields=["programme"])
            except Exception:
                pass
        project.context_revision += 1
        project.save()
    return project


def save_draft(actor, project_id, draft_data, expected_revision):
    """Create or update the editable topic draft."""
    from .models import TopicDraft

    with transaction.atomic():
        project = authorize_project(actor, project_id, "edit_draft")
        draft, created = TopicDraft.objects.select_for_update().get_or_create(
            project=project,
            defaults={
                "title": draft_data.get("title", ""),
                "hypothesis": draft_data.get("hypothesis", ""),
                "rationale": draft_data.get("rationale", ""),
                "source_ids": draft_data.get("source_ids", []),
                "revision": 1,
                "updated_by": actor,
            },
        )
        if not created:
            if draft.revision != expected_revision:
                raise Conflict("stale_revision")
            draft.title = draft_data.get("title", draft.title)
            draft.hypothesis = draft_data.get("hypothesis", draft.hypothesis)
            draft.rationale = draft_data.get("rationale", draft.rationale)
            draft.source_ids = draft_data.get("source_ids", draft.source_ids)
            draft.revision += 1
            draft.updated_by = actor
            draft.save()
    return draft


def save_source(actor, project_id, source_id):
    """Idempotently save a source to the project."""
    from .models import ProjectSource

    project = authorize_project(actor, project_id, "save_source")
    ps, _ = ProjectSource.objects.get_or_create(
        project=project,
        source_id=source_id,
        defaults={"saved_by": actor},
    )
    return ps


def remove_source(actor, project_id, source_id):
    """Remove a source link from the project."""
    from .models import ProjectSource

    project = authorize_project(actor, project_id, "save_source")
    ProjectSource.objects.filter(project=project, source_id=source_id).delete()


def submit_topic(actor, project_id, draft_revision, idempotency_key=None):
    """Create an immutable topic submission from the current draft.

    Requires an accepted supervisor assignment.
    Raises Conflict if submission already in review.
    """
    from supervision.models import SupervisorAssignment

    from .models import Project, TopicDraft, TopicSubmission

    with transaction.atomic():
        project = authorize_project(actor, project_id, "submit_topic")

        # Must have accepted supervisor
        assignment = SupervisorAssignment.objects.filter(
            project_id=project_id,
            state="accepted",
        ).first()
        if not assignment:
            raise Conflict("no_active_assignment")

        # No pending undecided submission
        latest = TopicSubmission.objects.filter(project=project).order_by("-number").first()
        if latest and latest.display_state() == "submitted":
            raise Conflict("pending_submission")

        # Load draft
        try:
            draft = TopicDraft.objects.get(project=project)
        except TopicDraft.DoesNotExist:
            raise Conflict("no_draft")

        if draft.revision != draft_revision:
            raise Conflict("stale_revision")

        # Build source snapshot from saved project sources
        from recommendations.models import Source

        saved_ids = list(
            project.project_sources.values_list("source_id", flat=True)
        )
        sources_snapshot = list(
            Source.objects.filter(id__in=saved_ids).values(
                "id", "title", "authors", "year", "url", "annotation", "evidence_level"
            )
        )

        # Allocate submission number
        number = (TopicSubmission.objects.filter(project=project).count()) + 1

        submission = TopicSubmission.objects.create(
            id=make_uuid(),
            project=project,
            assignment=assignment,
            number=number,
            title=draft.title,
            hypothesis=draft.hypothesis,
            rationale=draft.rationale,
            sources_snapshot=sources_snapshot,
            author=actor,
        )

    from governance.services import audit
    audit(actor, "topic.submitted", "TopicSubmission", submission.id, project_id=project_id)
    return submission


def get_project_overview(actor, project_id):
    """Gather all related data for the project overview page."""
    from supervision.models import SupervisorAssignment

    project = authorize_project(actor, project_id, "view")

    # Topic draft
    try:
        draft = project.topic_draft
    except Exception:
        draft = None

    # Latest submission
    latest_submission = project.submissions.order_by("-number").first()

    # Supervisor assignment
    active_assignment = SupervisorAssignment.objects.filter(
        project_id=project_id,
        state__in=["requested", "accepted"],
    ).select_related("supervisor").first()

    # Latest document version
    latest_version = None
    latest_check = None
    try:
        from documents.models import CheckRun, DocumentVersion
        latest_version = DocumentVersion.objects.filter(
            project_id=str(project.id)
        ).order_by("-number").first()
        if latest_version:
            latest_check = CheckRun.objects.filter(
                version=latest_version
            ).order_by("-created_at").first()
    except Exception:
        pass

    # Recent sources count
    source_count = project.project_sources.count()

    # Latest AI generation
    latest_gen = project.topic_generations.order_by("-created_at").first()

    return {
        "project": project,
        "draft": draft,
        "latest_submission": latest_submission,
        "submission_state": latest_submission.display_state() if latest_submission else None,
        "active_assignment": active_assignment,
        "source_count": source_count,
        "latest_version": latest_version,
        "latest_check": latest_check,
        "latest_gen": latest_gen,
    }

"""Supervision domain services: assignments, topic decisions, comments."""
import logging

from django.db import transaction
from django.utils import timezone

from core.errors import Conflict, Forbidden, NotFound, ValidationError
from core.utils import make_uuid

logger = logging.getLogger(__name__)


def request_supervision(actor, project_id, supervisor_id):
    """Create a supervision request. One active request/project at a time."""
    from accounts.models import SupervisorProfile
    from projects.services import authorize_project
    from supervision.models import SupervisorAssignment

    project = authorize_project(actor, project_id, "save_source")

    # Verify supervisor exists and is available
    try:
        profile = SupervisorProfile.objects.select_related("user").get(user_id=supervisor_id)
    except SupervisorProfile.DoesNotExist:
        raise NotFound("supervisor", supervisor_id)

    if not profile.available or profile.capacity <= 0:
        raise Conflict("supervisor_unavailable")

    # Check no active request/assignment
    existing = SupervisorAssignment.objects.filter(
        project_id=project_id,
        state__in=[SupervisorAssignment.STATE_REQUESTED, SupervisorAssignment.STATE_ACCEPTED],
    ).first()
    if existing:
        raise Conflict("duplicate_request")

    assignment = SupervisorAssignment.objects.create(
        id=make_uuid(),
        project_id=project_id,
        supervisor_id=supervisor_id,
        requested_by=actor,
        state=SupervisorAssignment.STATE_REQUESTED,
    )
    from governance.services import audit
    audit(actor, "supervision.requested", "SupervisorAssignment", assignment.id,
          project_id=project_id, supervisor_id=supervisor_id)
    return assignment


def cancel_request(actor, assignment_id):
    """Student cancels a pending request."""
    from supervision.models import SupervisorAssignment

    try:
        assignment = SupervisorAssignment.objects.get(pk=assignment_id)
    except SupervisorAssignment.DoesNotExist:
        raise NotFound("assignment", assignment_id)

    if assignment.requested_by_id != actor.id:
        raise Forbidden("cancel_request")
    if assignment.state != SupervisorAssignment.STATE_REQUESTED:
        raise Conflict("cannot_cancel")

    assignment.state = SupervisorAssignment.STATE_CANCELLED
    assignment.responded_at = timezone.now()
    assignment.save(update_fields=["state", "responded_at"])
    return assignment


def respond_to_request(actor, assignment_id, accepted: bool, note: str = ""):
    """Supervisor accepts or declines a request."""
    from supervision.models import SupervisorAssignment

    try:
        assignment = SupervisorAssignment.objects.select_related("supervisor").get(pk=assignment_id)
    except SupervisorAssignment.DoesNotExist:
        raise NotFound("assignment", assignment_id)

    if assignment.supervisor_id != actor.id:
        raise Forbidden("respond_to_request")
    if assignment.state != SupervisorAssignment.STATE_REQUESTED:
        raise Conflict("cannot_respond")

    assignment.state = SupervisorAssignment.STATE_ACCEPTED if accepted else SupervisorAssignment.STATE_DECLINED
    assignment.responded_at = timezone.now()
    assignment.note = note or ""
    assignment.save(update_fields=["state", "responded_at", "note"])

    from governance.services import audit
    audit(actor, "supervision.responded", "SupervisorAssignment", assignment.id,
          accepted=str(accepted))
    return assignment


def decide_topic(actor, submission_id, decision: str, comment: str):
    """Accepted supervisor makes an immutable decision on a topic submission."""
    from projects.models import TopicDecision, TopicSubmission
    from projects.services import authorize_project
    from supervision.models import SupervisorAssignment

    if decision not in (TopicDecision.APPROVED, TopicDecision.CHANGES_REQUESTED, TopicDecision.REJECTED):
        raise ValidationError({"decision": "Nepareizs lēmums."})

    if decision in (TopicDecision.CHANGES_REQUESTED, TopicDecision.REJECTED) and not comment.strip():
        raise ValidationError({"comment": "Komentārs ir obligāts noraidot vai pieprasot labojumus."})

    try:
        submission = TopicSubmission.objects.select_related("project", "assignment").get(pk=submission_id)
    except TopicSubmission.DoesNotExist:
        raise NotFound("submission", submission_id)

    # Verify actor has accepted assignment for this project
    project = submission.project
    assignment = SupervisorAssignment.objects.filter(
        project_id=project.id,
        supervisor_id=actor.id,
        state=SupervisorAssignment.STATE_ACCEPTED,
    ).first()
    if not assignment:
        raise Forbidden("decide_topic")

    # Must be current submission (highest number)
    latest = project.submissions.order_by("-number").first()
    if latest and latest.pk != submission.pk:
        raise Conflict("not_current_submission")

    # Check not already decided
    if hasattr(submission, "decision"):
        try:
            _ = submission.decision
            raise Conflict("already_decided")
        except TopicDecision.DoesNotExist:
            pass

    with transaction.atomic():
        td = TopicDecision.objects.create(
            id=make_uuid(),
            submission=submission,
            actor=actor,
            decision=decision,
            comment=comment,
        )

    from governance.services import audit
    audit(actor, "topic.decided", "TopicDecision", td.id,
          project_id=str(project.id), decision=decision)
    return td


def add_comment(actor, project_id, version_id=None, finding_id=None, text: str = ""):
    """Add a comment to a document version or finding."""
    from projects.services import authorize_project
    from supervision.models import Comment

    project = authorize_project(actor, project_id)

    if not text.strip():
        raise ValidationError({"text": "Komentārs nevar būt tukšs."})
    if bool(version_id) == bool(finding_id):
        raise ValidationError({"target": "Nepieciešams vai nu versijas ID, vai atraduma ID."})

    # Verify target belongs to this project
    if version_id:
        from documents.models import DocumentVersion
        try:
            dv = DocumentVersion.objects.get(pk=version_id, project_id=project_id)
        except DocumentVersion.DoesNotExist:
            raise NotFound("version", version_id)
    if finding_id:
        from documents.models import Finding
        try:
            f = Finding.objects.select_related("run__version").get(pk=finding_id)
            if f.run.version.project_id != project_id:
                raise NotFound("finding", finding_id)
        except Finding.DoesNotExist:
            raise NotFound("finding", finding_id)

    comment = Comment.objects.create(
        id=make_uuid(),
        actor=actor,
        version_id=version_id,
        finding_id=finding_id,
        text=text.strip(),
    )
    return comment


def claim_fixed(actor, project_id, finding_id, note: str = ""):
    """Student marks a finding as claimed fixed."""
    from projects.services import authorize_project
    from documents.models import Finding, FindingResolution

    project = authorize_project(actor, project_id, "resolve")

    try:
        finding = Finding.objects.select_related("run__version").get(pk=finding_id)
        if finding.run.version.project_id != project_id:
            raise NotFound("finding", finding_id)
    except Finding.DoesNotExist:
        raise NotFound("finding", finding_id)

    resolution, _ = FindingResolution.objects.update_or_create(
        finding=finding,
        defaults={
            "actor": actor,
            "state": FindingResolution.STATE_CLAIMED,
            "note": note.strip(),
        },
    )
    return resolution

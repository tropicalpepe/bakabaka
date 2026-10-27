import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.errors import BakaBakaError, Conflict, ValidationError, error_response, success_response
from governance.services import get_active_ruleset
from projects.services import (
    authorize_project,
    get_project_overview,
    remove_source,
    save_context,
    save_draft,
    save_source,
    submit_topic,
)

from .models import Project, TopicDraft, TopicSubmission

logger = logging.getLogger(__name__)


@login_required
def overview(request):
    """List projects for the current user."""
    user = request.user
    if user.role == "student":
        projects = Project.objects.filter(owner=user).order_by("-updated_at")
    elif user.role == "supervisor":
        from supervision.models import SupervisorAssignment
        project_ids = SupervisorAssignment.objects.filter(
            supervisor=user,
            state__in=["requested", "accepted"],
        ).values_list("project_id", flat=True)
        projects = Project.objects.filter(id__in=project_ids).order_by("-updated_at")
    else:
        projects = Project.objects.none()

    return render(request, "projects/overview.html", {"projects": projects})


@login_required
def new_project(request):
    """Create a new project."""
    ruleset = get_active_ruleset()
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        idea = request.POST.get("idea", "").strip()
        keywords_raw = request.POST.get("keywords", "").strip()
        programme = request.POST.get("programme", "").strip()

        errors = {}
        if not title:
            errors["title"] = "Ievadiet projekta nosaukumu."
        if not idea:
            errors["idea"] = "Ievadiet pētnieciskā darba ideju."
        if not programme:
            errors["programme"] = "Izvēlieties programmu."

        if errors:
            return render(request, "projects/new_project.html", {
                "errors": errors,
                "form": request.POST,
                "ruleset": ruleset,
            })

        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
        project = Project.objects.create(
            owner=request.user,
            requirement_set=ruleset,
            title=title,
            idea=idea,
            keywords=keywords,
        )
        # Update student profile
        try:
            request.user.student_profile.programme = programme
            request.user.student_profile.keywords = keywords
            request.user.student_profile.save(update_fields=["programme", "keywords"])
        except Exception:
            pass

        from governance.services import audit
        audit(request.user, "project.created", "Project", project.id)
        return redirect("projects:detail", project_id=project.id)

    return render(request, "projects/new_project.html", {"ruleset": ruleset})


@login_required
def project_detail(request, project_id):
    """Full project overview page."""
    try:
        data = get_project_overview(request.user, project_id)
    except BakaBakaError as exc:
        return redirect("projects:overview")

    data["current_project"] = data["project"]  # enables section nav in base.html
    return render(request, "projects/detail.html", data)


@login_required
@require_http_methods(["POST"])
def edit_context(request, project_id):
    """JSON endpoint: update project context."""
    try:
        body = json.loads(request.body)
        title = body.get("title", "").strip()
        idea = body.get("idea", "").strip()
        keywords = body.get("keywords", [])
        expected_revision = body.get("expected_revision")
        programme = body.get("programme", "")

        if not title:
            raise ValidationError({"title": "Nepieciešams nosaukums."})
        if expected_revision is None:
            raise ValidationError({"expected_revision": "Nepieciešama pašreizējā versija."})

        project = save_context(
            request.user, project_id, title, idea, keywords,
            int(expected_revision), programme
        )
        return success_response({
            "id": project.id,
            "title": project.title,
            "idea": project.idea,
            "keywords": project.keywords,
            "context_revision": project.context_revision,
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_save_draft(request, project_id):
    """JSON endpoint: save topic draft."""
    try:
        body = json.loads(request.body)
        draft = save_draft(
            request.user,
            project_id,
            {
                "title": body.get("title", ""),
                "hypothesis": body.get("hypothesis", ""),
                "rationale": body.get("rationale", ""),
                "source_ids": body.get("source_ids", []),
            },
            int(body.get("expected_revision", 1)),
        )
        return success_response({
            "project_id": str(draft.project_id),
            "title": draft.title,
            "hypothesis": draft.hypothesis,
            "rationale": draft.rationale,
            "source_ids": draft.source_ids,
            "revision": draft.revision,
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_save_source(request, project_id):
    """JSON endpoint: save or remove a source from the project."""
    try:
        body = json.loads(request.body)
        source_id = body.get("source_id", "").strip()
        action = body.get("action", "save")
        if not source_id:
            raise ValidationError({"source_id": "Nepieciešams avota ID."})
        if action == "remove":
            remove_source(request.user, project_id, source_id)
            return success_response({"removed": source_id})
        ps = save_source(request.user, project_id, source_id)
        return success_response({"saved": source_id})
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_submit_topic(request, project_id):
    """JSON endpoint: submit topic for supervisor review."""
    try:
        body = json.loads(request.body)
        draft_revision = int(body.get("draft_revision", 1))
        submission = submit_topic(request.user, project_id, draft_revision)
        return success_response({
            "id": submission.id,
            "number": submission.number,
            "submitted_at": submission.submitted_at.isoformat(),
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
def api_job_status(request, job_id):
    """Poll job status (GET)."""
    from governance.models import Job
    try:
        project_id = request.GET.get("project_id")
        job = Job.objects.get(pk=job_id)
        # Verify caller can see this job's project
        if project_id and job.project_id != project_id:
            return JsonResponse({"error": "not_found"}, status=404)
        authorize_project(request.user, job.project_id, "view")
        return JsonResponse({
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "result_id": job.result_id,
            "error_code": job.error_code,
            "attempts": job.attempts,
        })
    except Exception:
        return JsonResponse({"error": "not_found"}, status=404)

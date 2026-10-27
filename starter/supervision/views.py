import json
import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.errors import BakaBakaError, ValidationError, error_response, success_response
from projects.services import authorize_project

from .services import (
    add_comment,
    cancel_request,
    claim_fixed,
    decide_topic,
    request_supervision,
    respond_to_request,
)

logger = logging.getLogger(__name__)


@login_required
def index(request, project_id):
    """Supervision index: student sees request status; supervisor sees review panel."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError:
        return redirect("projects:overview")

    from accounts.models import SupervisorProfile
    from projects.models import TopicDecision, TopicSubmission
    from supervision.models import SupervisorAssignment

    # All assignments for this project (history)
    assignments = SupervisorAssignment.objects.filter(
        project_id=project_id
    ).select_related("supervisor", "requested_by").order_by("-requested_at")

    active = assignments.filter(state__in=["requested", "accepted"]).first()

    # Latest submission
    latest_submission = (
        TopicSubmission.objects.filter(project=project)
        .prefetch_related("decision")
        .order_by("-number")
        .first()
    )

    # Submission history
    submissions = (
        TopicSubmission.objects.filter(project=project)
        .order_by("-number")
        .prefetch_related("decision")[:10]
    )

    return render(request, "supervision/index.html", {
        "project": project,
        "assignments": assignments,
        "active_assignment": active,
        "latest_submission": latest_submission,
        "submissions": submissions,
        "current_project": project,
    })


@login_required
def supervisor_inbox(request):
    """Supervisor's view of all their assignments."""
    if request.user.role != "supervisor":
        return redirect("projects:overview")

    from supervision.models import SupervisorAssignment

    assignments = SupervisorAssignment.objects.filter(
        supervisor=request.user
    ).select_related("requested_by").order_by("-requested_at")[:50]

    # For each assignment, get project info
    from projects.models import Project

    project_ids = [a.project_id for a in assignments]
    projects = {p.id: p for p in Project.objects.filter(id__in=project_ids)}

    enriched = [(a, projects.get(a.project_id)) for a in assignments]

    return render(request, "supervision/inbox.html", {
        "enriched": enriched,
    })


@login_required
@require_http_methods(["POST"])
def api_request(request, project_id):
    """JSON: request supervision."""
    try:
        body = json.loads(request.body)
        supervisor_id = int(body.get("supervisor_id", 0))
        assignment = request_supervision(request.user, project_id, supervisor_id)
        return success_response({
            "id": assignment.id,
            "state": assignment.state,
            "supervisor_id": assignment.supervisor_id,
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError) as exc:
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_respond(request, assignment_id):
    """JSON: accept or decline a supervision request."""
    try:
        body = json.loads(request.body)
        accepted = bool(body.get("accepted", False))
        note = body.get("note", "")
        assignment = respond_to_request(request.user, assignment_id, accepted, note)
        return success_response({"id": assignment.id, "state": assignment.state})
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_cancel(request, assignment_id):
    """JSON: student cancels a pending request."""
    try:
        assignment = cancel_request(request.user, assignment_id)
        return success_response({"id": assignment.id, "state": assignment.state})
    except BakaBakaError as exc:
        return error_response(exc)


@login_required
@require_http_methods(["POST"])
def api_decide(request, project_id, submission_id):
    """JSON: supervisor decides on a submitted topic."""
    try:
        body = json.loads(request.body)
        decision = body.get("decision", "").strip()
        comment = body.get("comment", "").strip()
        td = decide_topic(request.user, submission_id, decision, comment)
        return success_response({
            "id": td.id,
            "decision": td.decision,
            "comment": td.comment,
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_comment(request, project_id):
    """JSON: add comment to version or finding."""
    try:
        body = json.loads(request.body)
        text = body.get("text", "").strip()
        version_id = body.get("version_id") or None
        finding_id = body.get("finding_id") or None
        comment = add_comment(request.user, project_id, version_id, finding_id, text)
        return success_response({
            "id": comment.id,
            "text": comment.text,
            "actor": request.user.get_full_name() or request.user.username,
            "created_at": comment.created_at.isoformat(),
        })
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))


@login_required
@require_http_methods(["POST"])
def api_claim_fixed(request, project_id, finding_id):
    """JSON: student claims a finding is fixed."""
    try:
        body = json.loads(request.body) if request.body else {}
        note = body.get("note", "")
        resolution = claim_fixed(request.user, project_id, finding_id, note)
        return success_response({"state": resolution.state, "note": resolution.note})
    except BakaBakaError as exc:
        return error_response(exc)
    except (json.JSONDecodeError, ValueError):
        return error_response(ValidationError(message="Nepareizs pieprasījuma formāts."))

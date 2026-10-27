import logging

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.errors import BakaBakaError, ValidationError, error_response, success_response
from projects.services import authorize_project

from .services import accept_upload, download_version, queue_advice, queue_check

logger = logging.getLogger(__name__)


@login_required
def index(request, project_id):
    """Documents list and upload page."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError:
        return redirect("projects:overview")

    from .models import DocumentVersion

    versions = DocumentVersion.objects.filter(
        project_id=project_id
    ).prefetch_related("extraction", "check_runs", "advice_runs").order_by("-number")

    return render(request, "documents/index.html", {
        "project": project,
        "versions": versions,
        "current_project": project,
        "user_is_student": request.user.role == "student",
    })


@login_required
def version_detail(request, project_id, version_id):
    """Detailed view of a single document version."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError:
        return redirect("projects:overview")

    from .models import AdviceRun, CheckRun, DocumentVersion, Finding, FindingResolution, RuleResult
    from supervision.models import Comment

    try:
        version = DocumentVersion.objects.get(pk=version_id, project_id=project_id)
    except DocumentVersion.DoesNotExist:
        return redirect("documents:index", project_id=project_id)

    # Latest check run
    check_runs = CheckRun.objects.filter(version=version).order_by("-created_at").prefetch_related("rule_results")
    latest_check = check_runs.first()

    # Findings with resolutions
    findings_with_resolutions = []
    if latest_check and latest_check.state == CheckRun.STATE_SUCCEEDED:
        findings = list(latest_check.findings.all())
        resolutions = {
            r.finding_id: r for r in FindingResolution.objects.filter(finding__in=findings)
        }
        findings_with_resolutions = [(f, resolutions.get(f.id)) for f in findings]

    # Latest advice run
    advice_runs = AdviceRun.objects.filter(version=version).order_by("-created_at")
    latest_advice = advice_runs.first()

    # Comments on this version
    version_comments = Comment.objects.filter(version_id=version_id).select_related("actor").order_by("created_at")

    # Check/advice jobs
    from governance.models import Job
    pending_check_job = Job.objects.filter(
        project_id=project_id, kind="checks.run", state__in=["queued", "running"]
    ).first()
    pending_advice_job = Job.objects.filter(
        project_id=project_id, kind="advice.generate", state__in=["queued", "running"]
    ).first()

    return render(request, "documents/version_detail.html", {
        "project": project,
        "version": version,
        "check_runs": check_runs,
        "latest_check": latest_check,
        "findings_with_resolutions": findings_with_resolutions,
        "advice_runs": advice_runs,
        "latest_advice": latest_advice,
        "version_comments": version_comments,
        "pending_check_job": pending_check_job,
        "pending_advice_job": pending_advice_job,
        "current_project": project,
        "user_is_student": request.user.role == "student",
    })


@login_required
@require_http_methods(["POST"])
def upload(request, project_id):
    """Handle file upload."""
    try:
        project = authorize_project(request.user, project_id, "upload")
    except BakaBakaError:
        return redirect("projects:overview")

    if "file" not in request.FILES:
        from django.contrib import messages
        messages.error(request, "Lūdzu izvēlieties failu.")
        return redirect("documents:index", project_id=project_id)

    f = request.FILES["file"]
    original_name = f.name

    try:
        version, job = accept_upload(
            request.user,
            project_id,
            f,
            original_name,
            idempotency_key=request.POST.get("idempotency_key"),
        )
        return redirect("documents:version_detail", project_id=project_id, version_id=version.id)
    except BakaBakaError as exc:
        from django.contrib import messages
        from core.errors import lv_message
        msg = lv_message(exc.reason if hasattr(exc, "reason") else str(exc))
        messages.error(request, msg)
        return redirect("documents:index", project_id=project_id)
    except Exception as exc:
        logger.exception("Upload error: %s", exc)
        from django.contrib import messages
        messages.error(request, "Augšupielāde neizdevās. Pārbaudiet faila formātu un izmēru.")
        return redirect("documents:index", project_id=project_id)


@login_required
def download(request, project_id, version_id):
    """Authorized file download."""
    try:
        path, filename = download_version(request.user, version_id)
        return FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename=filename,
        )
    except BakaBakaError as exc:
        return error_response(exc)


@login_required
@require_http_methods(["POST"])
def api_queue_check(request, project_id, version_id):
    """JSON: queue a check run."""
    try:
        run, job = queue_check(request.user, project_id, version_id)
        return success_response({"run_id": run.id, "job_id": job.id})
    except BakaBakaError as exc:
        return error_response(exc)


@login_required
@require_http_methods(["POST"])
def api_queue_advice(request, project_id, version_id):
    """JSON: queue an advice run."""
    try:
        advice_run, job = queue_advice(request.user, project_id, version_id)
        return success_response({"advice_run_id": advice_run.id, "job_id": job.id})
    except BakaBakaError as exc:
        return error_response(exc)

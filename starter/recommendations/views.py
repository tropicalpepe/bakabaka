import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.errors import BakaBakaError, ValidationError, error_response, success_response
from core.utils import fingerprint, make_uuid
from projects.services import authorize_project, save_source, remove_source

from .models import Source, SourceEmbedding
from .search import load_corpus, match_supervisors, search_sources

logger = logging.getLogger(__name__)


@login_required
def research(request, project_id):
    """Main research page: topic generation + source search."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError:
        return redirect("projects:overview")

    # Load topic draft and latest generation
    draft = getattr(project, "topic_draft", None)
    latest_gen = project.topic_generations.order_by("-created_at").first()

    # Saved sources
    saved_ids = set(project.project_sources.values_list("source_id", flat=True))
    saved_sources = list(Source.objects.filter(id__in=saved_ids)) if saved_ids else []

    # Supervisor candidates
    candidates = match_supervisors(project)

    # Check if embeddings indexed
    embedding_count = SourceEmbedding.objects.count()

    # Get any pending generation job
    from governance.models import Job
    pending_job = Job.objects.filter(
        project_id=project_id, kind="topics.generate",
        state__in=["queued", "running"]
    ).first()

    return render(request, "recommendations/research.html", {
        "project": project,
        "draft": draft,
        "latest_gen": latest_gen,
        "saved_sources": saved_sources,
        "saved_ids": saved_ids,
        "candidates": candidates,
        "embedding_count": embedding_count,
        "pending_job": pending_job,
        "current_project": project,
    })


@login_required
@require_http_methods(["GET"])
def api_search(request, project_id):
    """JSON: search sources."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError as exc:
        return error_response(exc)

    query = request.GET.get("q", "").strip()
    filters = {
        "year_from": request.GET.get("year_from") or None,
        "year_to": request.GET.get("year_to") or None,
        "repository": request.GET.get("repository") or None,
        "language": request.GET.get("language") or None,
        "document_type": request.GET.get("document_type") or None,
    }

    result = search_sources(query, filters)
    saved_ids = set(project.project_sources.values_list("source_id", flat=True))

    sources_data = []
    for s in result["results"]:
        sources_data.append({
            "id": s["id"],
            "title": s["title"],
            "authors": s["authors"],
            "year": s["year"],
            "url": s["url"],
            "repository": s["repository"],
            "language": s["language"],
            "document_type": s["document_type"],
            "annotation": s["annotation"],
            "evidence_level": s["evidence_level"],
            "synthetic": s["synthetic"],
            "saved": s["id"] in saved_ids,
        })

    return JsonResponse({
        "results": sources_data,
        "mode": result["mode"],
        "degraded_reason": result["degraded_reason"],
        "total": result["total"],
    })


@login_required
@require_http_methods(["POST"])
def api_queue_topics(request, project_id):
    """JSON: queue a topic generation job."""
    try:
        project = authorize_project(request.user, project_id, "edit_draft")
    except BakaBakaError as exc:
        return error_response(exc)

    body = json.loads(request.body) if request.body else {}
    expected_revision = body.get("expected_revision", project.context_revision)

    # Check for insufficient grounding
    from .search import search_sources
    result = search_sources(project.idea + " " + " ".join(project.keywords))
    scholarly = [s for s in result["results"] if not s.get("synthetic")]

    # Build fingerprint from context
    ctx_fp = fingerprint(project.title, project.idea, str(project.keywords), str(expected_revision))

    # Create TopicGeneration placeholder
    from projects.models import TopicGeneration
    gen = TopicGeneration.objects.create(
        id=make_uuid(),
        project=project,
        context_fingerprint=ctx_fp,
        model=__import__("django.conf", fromlist=["settings"]).settings.OPENAI_MODEL,
        origin="openai_live",
        proposals_json=[],
        source_ids=[s["id"] for s in scholarly[:5]],
        limitations="Notiek ģenerēšana...",
    )

    # Mark previous generations stale if context changed
    from projects.models import TopicGeneration as TG
    TG.objects.filter(project=project).exclude(pk=gen.pk).update(is_stale=True)

    from jobs.engine import enqueue
    job, created = enqueue(
        project_id=project_id,
        creator_id=request.user.id,
        kind="topics.generate",
        fingerprint=ctx_fp,
        payload={"project_id": project_id, "generation_id": gen.id},
    )

    # Link job to generation
    gen.job = job
    gen.save(update_fields=["job"])

    return success_response({
        "job_id": job.id,
        "generation_id": gen.id,
        "job_created": created,
    })


@login_required
def api_generation_result(request, project_id, generation_id):
    """JSON: get topic generation result."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError as exc:
        return error_response(exc)

    from projects.models import TopicGeneration
    try:
        gen = TopicGeneration.objects.get(pk=generation_id, project=project)
    except TopicGeneration.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    # Check source details
    source_ids = gen.source_ids or []
    sources = {s.id: {"title": s.title, "url": s.url, "year": s.year, "authors": s.authors}
               for s in Source.objects.filter(id__in=source_ids)}

    job_state = None
    job_error = None
    if gen.job:
        job_state = gen.job.state
        job_error = gen.job.error_code

    return JsonResponse({
        "id": gen.id,
        "origin": gen.origin,
        "is_stale": gen.is_stale,
        "model": gen.model,
        "prompt_version": gen.prompt_version,
        "proposals": gen.proposals_json,
        "grounding_note": gen.limitations,
        "sources": sources,
        "created_at": gen.created_at.isoformat(),
        "job_state": job_state,
        "job_error": job_error,
    })


@login_required
@require_http_methods(["GET"])
def api_supervisors(request, project_id):
    """JSON: get supervisor candidate list."""
    try:
        project = authorize_project(request.user, project_id)
    except BakaBakaError as exc:
        return error_response(exc)

    candidates = match_supervisors(project)
    result = []
    for c in candidates:
        u = c["user"]
        p = c["profile"]
        result.append({
            "user_id": u.id,
            "display_name": u.get_full_name() or u.username,
            "competencies": p.competencies,
            "keywords": p.keywords,
            "available": p.available,
            "capacity": p.capacity,
            "score": c["score"],
            "matched_keywords": c["matched_keywords"],
            "institution": p.institution,
            "synthetic": u.synthetic,
        })
    return JsonResponse({"candidates": result})

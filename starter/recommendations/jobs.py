"""Job handlers for the recommendations module.

Registered handlers:
  topics.generate  — generate 3 topic proposals via OpenAI
  sources.embed    — compute and cache source embeddings
"""
import hashlib
import json
import logging

from django.conf import settings

from jobs.registry import register

logger = logging.getLogger(__name__)


def _handle_topics_generate(job):
    """Generate topic proposals for a project and persist TopicGeneration."""
    from projects.models import Project, TopicDraft, TopicGeneration
    from recommendations.gateway import AIError, generate_topics
    from recommendations.models import Source
    from recommendations.search import search_sources
    from core.utils import make_uuid, fingerprint

    payload = job.payload_json
    project_id = payload.get("project_id") or job.project_id
    generation_id = payload.get("generation_id")

    project = Project.objects.select_related("owner").get(pk=project_id)

    # Load or create TopicGeneration record to track state
    try:
        gen = TopicGeneration.objects.get(pk=generation_id)
    except TopicGeneration.DoesNotExist:
        return None

    # Build context
    context = {
        "programme": getattr(project.owner.student_profile, "programme", "") if hasattr(project.owner, "student_profile") else "",
        "interests": getattr(project.owner.student_profile, "interests", []) if hasattr(project.owner, "student_profile") else [],
        "keywords": project.keywords,
        "title": project.title,
        "idea": project.idea,
    }

    # Retrieve top scholarly sources
    search_result = search_sources(project.idea + " " + " ".join(project.keywords))
    scholarly = [s for s in search_result["results"] if not s.get("synthetic")][:5]

    model = settings.OPENAI_MODEL

    try:
        response = generate_topics(context, scholarly, model=model)
        batch = response.output_parsed
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {"input_tokens": getattr(response.usage, "input_tokens", 0),
                     "output_tokens": getattr(response.usage, "output_tokens", 0)}
        response_id = getattr(response, "id", None)

        TopicGeneration.objects.filter(pk=gen.pk).update(
            origin="openai_live",
            model=model,
            proposals_json=[p.model_dump() for p in batch.proposals],
            source_ids=[sid for p in batch.proposals for sid in p.source_ids],
            limitations=batch.grounding_note,
            usage_json=usage,
            response_id=response_id,
        )
        return gen.pk

    except AIError as exc:
        from governance.models import Job
        Job.objects.filter(pk=job.pk).update(error_code=exc.code)
        TopicGeneration.objects.filter(pk=gen.pk).update(
            proposals_json=[], limitations=f"Kļūda: {exc.code}"
        )
        raise RuntimeError(exc.code) from exc


def _handle_sources_embed(job):
    """Compute embeddings for all unembedded scholarly sources."""
    from recommendations.gateway import AIError, embed_texts
    from recommendations.models import Source, SourceEmbedding
    from core.utils import content_hash

    model = settings.OPENAI_EMBEDDING_MODEL
    dims = settings.OPENAI_EMBEDDING_DIMENSIONS

    sources = Source.objects.filter(synthetic=False)
    to_embed = []
    for s in sources:
        text = f"{s.title}. {s.annotation}"
        chash = hashlib.sha256(text.encode()).hexdigest()
        exists = SourceEmbedding.objects.filter(source=s, model=model, dimensions=dims, content_hash=chash).exists()
        if not exists:
            to_embed.append((s, text, chash))

    if not to_embed:
        logger.info("All sources already embedded.")
        return "no_work"

    # Batch in groups of 20
    BATCH = 20
    embedded = 0
    for i in range(0, len(to_embed), BATCH):
        batch = to_embed[i:i + BATCH]
        texts = [t for _, t, _ in batch]
        try:
            vectors = embed_texts(texts, model=model, dimensions=dims)
        except AIError as exc:
            raise RuntimeError(f"Embedding failed: {exc.code}") from exc

        for (source, _, chash), vector in zip(batch, vectors):
            SourceEmbedding.objects.get_or_create(
                source=source,
                model=model,
                dimensions=dims,
                content_hash=chash,
                defaults={"vector_json": vector},
            )
            embedded += 1

    logger.info("Embedded %d sources.", embedded)
    return f"embedded_{embedded}"


register("topics.generate", _handle_topics_generate)
register("sources.embed", _handle_sources_embed)

"""Source search and retrieval service.

Implements lexical search always; semantic search when embeddings available.
Falls back to keyword-only mode with a visible label.
"""
import hashlib
import json
import logging
import math
import re
import unicodedata
from collections import Counter

from django.conf import settings

from .models import QueryEmbedding, Source, SourceEmbedding

logger = logging.getLogger(__name__)

HYBRID_K = 60


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def lexical_rank(query: str, sources: list) -> list[tuple[str, float]]:
    """Return (source_id, score) pairs sorted by keyword relevance."""
    q = Counter(_tokens(query))
    scored = []
    for s in sources:
        title_tokens = Counter(_tokens(s["title"]))
        anno_tokens = Counter(_tokens(s.get("annotation", "")))
        kw_tokens = Counter(_tokens(" ".join(s.get("authors", []))))
        score = sum(n * (3 * title_tokens[t] + anno_tokens[t] + kw_tokens[t]) for t, n in q.items())
        if score > 0:
            scored.append((s["id"], float(score)))
    return sorted(scored, key=lambda x: (-x[1], x[0]))


def _cosine(a: list, b: list) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    denom = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / denom if denom else 0.0


def _rrf(lexical: list, semantic: list, k: int = HYBRID_K) -> list[tuple[str, float]]:
    combined = {}
    for ranking in (lexical, semantic):
        for pos, (sid, _) in enumerate(ranking, 1):
            combined[sid] = combined.get(sid, 0) + 1 / (k + pos)
    return sorted(combined.items(), key=lambda x: (-x[1], x[0]))


def _query_hash(query: str, model: str, dimensions: int) -> str:
    text = f"{model}:{dimensions}:{unicodedata.normalize('NFKC', query.strip().casefold())}"
    return hashlib.sha256(text.encode()).hexdigest()


def get_query_vector(query: str) -> list | None:
    """Return cached or freshly computed query embedding. Returns None on failure."""
    model = settings.OPENAI_EMBEDDING_MODEL
    dims = settings.OPENAI_EMBEDDING_DIMENSIONS
    qhash = _query_hash(query, model, dims)

    cached = QueryEmbedding.objects.filter(query_hash=qhash).first()
    if cached:
        return cached.vector_json

    # Try to compute
    try:
        import os

        from openai import OpenAI

        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return None
        client = OpenAI(timeout=30.0, max_retries=1, api_key=key)
        resp = client.embeddings.create(model=model, input=[query], dimensions=dims, encoding_format="float")
        vector = resp.data[0].embedding
        QueryEmbedding.objects.create(
            query_hash=qhash, model=model, dimensions=dims, vector_json=vector
        )
        return vector
    except Exception as exc:
        logger.warning("Embedding query failed: %s", exc)
        return None


def search_sources(query: str, filters: dict | None = None, actor=None) -> dict:
    """Search the source corpus. Returns {results, mode, degraded_reason}."""
    filters = filters or {}
    qs = Source.objects.all()

    # Filters
    if filters.get("year_from"):
        qs = qs.filter(year__gte=int(filters["year_from"]))
    if filters.get("year_to"):
        qs = qs.filter(year__lte=int(filters["year_to"]))
    if filters.get("repository"):
        qs = qs.filter(repository=filters["repository"])
    if filters.get("language"):
        qs = qs.filter(language=filters["language"])
    if filters.get("document_type"):
        qs = qs.filter(document_type=filters["document_type"])
    if not filters.get("include_synthetic"):
        # Default: show all but mark synthetic clearly
        pass

    sources = list(qs.values("id", "title", "authors", "year", "url", "repository",
                              "language", "document_type", "annotation", "evidence_level", "synthetic"))

    if not query.strip():
        # No query: return all in default order
        return {"results": sources[:30], "mode": "all", "degraded_reason": None, "total": len(sources)}

    # Lexical ranking
    lex = lexical_rank(query, sources)
    degraded_reason = None

    # Try semantic
    q_vec = get_query_vector(query)
    if q_vec:
        # Get source embeddings
        model = settings.OPENAI_EMBEDDING_MODEL
        dims = settings.OPENAI_EMBEDDING_DIMENSIONS
        embs = {
            e["source_id"]: e["vector_json"]
            for e in SourceEmbedding.objects.filter(
                source_id__in=[s["id"] for s in sources],
                model=model,
                dimensions=dims,
            ).values("source_id", "vector_json")
        }
        if embs:
            sem = sorted(
                [(sid, _cosine(q_vec, vec)) for sid, vec in embs.items()],
                key=lambda x: (-x[1], x[0]),
            )
            combined = _rrf(lex, sem)
            mode = "hybrid"
        else:
            combined = lex
            mode = "keyword_only"
            degraded_reason = "Semantiskā meklēšana nav pieejama (avotu vektori nav aprēķināti)."
    else:
        combined = lex
        mode = "keyword_only"
        degraded_reason = "Meklēšana pēc atslēgvārdiem (AI nav konfigurēts)."

    # Build ordered result list
    id_map = {s["id"]: s for s in sources}
    ranked_ids = [sid for sid, _ in combined]
    # Include unranked sources at the end (lexical fallback)
    all_ids = ranked_ids + [s["id"] for s in sources if s["id"] not in set(ranked_ids)]
    results = [id_map[sid] for sid in all_ids if sid in id_map][:30]

    return {"results": results, "mode": mode, "degraded_reason": degraded_reason, "total": len(sources)}


def match_supervisors(project) -> list:
    """Compute supervisor candidates ranked by keyword/competency overlap."""
    from accounts.models import SupervisorProfile

    project_kws = set(_tokens(" ".join(project.keywords or [])))
    if hasattr(project, "owner"):
        try:
            profile_kws = set(_tokens(" ".join(project.owner.student_profile.keywords or [])))
            project_kws |= profile_kws
        except Exception:
            pass

    profiles = SupervisorProfile.objects.filter(
        available=True, capacity__gt=0
    ).select_related("user")

    results = []
    for p in profiles:
        sup_kws = set(_tokens(" ".join(list(p.keywords or []) + list(p.competencies or []))))
        matched = project_kws & sup_kws
        results.append({
            "user": p.user,
            "profile": p,
            "score": len(matched),
            "matched_keywords": sorted(matched),
        })
    return sorted(results, key=lambda r: (-r["score"], r["user"].id))


def load_corpus(fixture_path=None) -> int:
    """Load sources.json into the Source table. Idempotent."""
    import hashlib as _hashlib

    from django.conf import settings as _s

    path = fixture_path or (_s.FIXTURE_DIR / "sources.json")
    data = json.loads(path.read_text())
    count = 0
    for row in data:
        raw = json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
        chash = _hashlib.sha256(raw).hexdigest()
        _, created = Source.objects.get_or_create(
            id=row["id"],
            defaults={
                "title": row.get("title", ""),
                "authors": row.get("authors", []),
                "year": row.get("year", 0),
                "url": row.get("url", ""),
                "repository": row.get("repository", ""),
                "language": row.get("language", ""),
                "document_type": row.get("document_type", ""),
                "annotation": row.get("annotation", ""),
                "evidence_level": row.get("evidence_level", "metadata_annotation"),
                "synthetic": row.get("synthetic", False),
                "provenance": row.get("provenance", {}),
                "content_hash": chash,
            },
        )
        if created:
            count += 1
    return count

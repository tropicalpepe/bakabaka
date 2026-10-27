"""OpenAI gateway for topic generation and document advice.

No persistence, authorization, or live calls at import time.
Error codes map to core.errors.IntegrationUnavailable.
"""
import json
import logging
import math
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

from django.conf import settings

# Ensure contracts.ai_models is importable in both Docker and local dev.
try:
    import contracts.ai_models  # noqa: F401  — check if already importable
except ImportError:
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)


class AIError(Exception):
    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _make_client():
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise AIError("missing_key")
    try:
        from openai import OpenAI
        import httpx

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        verify_ssl = os.environ.get("OPENAI_VERIFY_SSL", "0" if proxy else "1") == "1"
        http_client = httpx.Client(proxy=proxy, verify=verify_ssl) if proxy else None

        return OpenAI(timeout=60.0, max_retries=2, api_key=key, http_client=http_client)
    except Exception as exc:
        raise AIError("missing_key") from exc


def _read_prompt(name: str) -> str:
    prompts_dir = getattr(settings, "PROMPTS_DIR", Path(__file__).parents[2] / "prompts")
    return Path(prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def _parse(client, model, prompt, payload, schema_cls, output_cap):
    """Call client.responses.parse and return the response object."""
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError
        from pydantic import ValidationError as PydanticValidationError

        try:
            response = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                text_format=schema_cls,
                store=False,
                max_output_tokens=output_cap,
            )
        except APITimeoutError as exc:
            raise AIError("timeout", True) from exc
        except APIConnectionError as exc:
            raise AIError("connection", True) from exc
        except APIStatusError as exc:
            code = {
                401: "invalid_key",
                403: "access_denied",
                404: "model_unavailable",
                429: "rate_or_quota",
            }.get(exc.status_code, "provider_error")
            raise AIError(code, exc.status_code in (429,) or exc.status_code >= 500) from exc
        except PydanticValidationError as exc:
            raise AIError("invalid_output") from exc
    except ImportError as exc:
        raise AIError("missing_key") from exc

    # Check for refusal in output
    for item in response.output or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", None) == "refusal":
                raise AIError("refused")

    if response.status != "completed":
        raise AIError("incomplete")
    if response.output_parsed is None:
        raise AIError("invalid_output")
    return response


def validate_topics(batch, allowed_source_ids: set):
    """Validate three distinct grounded proposals."""
    if len(batch.proposals) != 3:
        raise AIError("invalid_output")
    titles = []
    for item in batch.proposals:
        for field in ("title", "hypothesis", "rationale", "limitations"):
            if not getattr(item, field, "").strip():
                raise AIError("invalid_output")
        if not item.source_ids:
            raise AIError("insufficient_grounding")
        invalid = set(item.source_ids) - allowed_source_ids
        if invalid:
            logger.warning("Invalid source IDs in proposal: %s", invalid)
            raise AIError("invalid_citation")
        title_norm = " ".join(item.title.casefold().split())
        if any(SequenceMatcher(None, title_norm, prior).ratio() > 0.92 for prior in titles):
            raise AIError("duplicate_proposals")
        titles.append(title_norm)
    return batch


def generate_topics(context: dict, sources: list, model: str | None = None):
    """Generate 3 topic proposals. Returns (response, batch).

    sources: list of Source-like dicts with id, title, authors, year, annotation, etc.
    """
    from contracts.ai_models import TopicBatch

    model = model or settings.OPENAI_MODEL
    scholarly = [s for s in sources if not s.get("synthetic", False)]
    if not scholarly:
        raise AIError("insufficient_grounding")

    client = _make_client()
    prompt = _read_prompt("topic-v1")
    response = _parse(client, model, prompt, {"context": context, "sources": scholarly[:5]}, TopicBatch, 3000)
    allowed = {s["id"] for s in scholarly[:5]}
    validate_topics(response.output_parsed, allowed)
    return response


def validate_advice(batch, units: list, allowed_sources: set):
    """Validate advice items against supplied unit content.

    Evidence must be a genuine substring of the unit text. Whitespace and Unicode
    are normalized before comparison to tolerate minor model formatting variations.
    """
    import re
    import unicodedata

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t)).strip()

    if len(batch.suggestions) > 8:
        raise AIError("invalid_output")
    if not batch.limitations.strip():
        raise AIError("invalid_output")
    by_id = {u["id"]: _norm(u["text"]) for u in units}
    for item in batch.suggestions:
        if item.unit_id not in by_id:
            raise AIError("invalid_evidence")
        if not item.evidence.strip():
            raise AIError("invalid_evidence")
        # Normalize both sides before substring check
        if _norm(item.evidence) not in by_id[item.unit_id]:
            raise AIError("invalid_evidence")
        if set(item.source_ids) - allowed_sources:
            raise AIError("invalid_citation")
        for field in ("suggestion", "rationale", "uncertainty"):
            if not getattr(item, field, "").strip():
                raise AIError("invalid_output")
    return batch


def generate_advice(units: list, findings: list, sources: list, model: str | None = None):
    """Generate advisory document feedback."""
    from contracts.ai_models import AdviceBatch

    model = model or settings.OPENAI_MODEL
    client = _make_client()
    prompt = _read_prompt("advice-v1")
    response = _parse(
        client, model, prompt,
        {"units": units, "findings": findings, "sources": sources},
        AdviceBatch, 4000,
    )
    allowed = {s["id"] for s in sources}
    validate_advice(response.output_parsed, units, allowed)
    return response


def embed_texts(texts: list[str], model: str | None = None, dimensions: int | None = None) -> list[list]:
    """Compute embeddings for a list of texts. Raises AIError on failure."""
    model = model or settings.OPENAI_EMBEDDING_MODEL
    dimensions = dimensions or settings.OPENAI_EMBEDDING_DIMENSIONS

    if not texts or any(not t.strip() for t in texts):
        raise ValueError("All texts must be non-empty")

    client = _make_client()
    try:
        from openai import OpenAI

        resp = client.embeddings.create(
            model=model, input=texts, dimensions=dimensions, encoding_format="float"
        )
    except Exception as exc:
        raise AIError("connection", True) from exc

    items = sorted(resp.data, key=lambda x: x.index)
    if [x.index for x in items] != list(range(len(texts))):
        raise AIError("invalid_embedding")

    vectors = [x.embedding for x in items]
    for v in vectors:
        if len(v) != dimensions or not all(math.isfinite(x) for x in v) or sum(x * x for x in v) == 0:
            raise AIError("invalid_embedding")
    return vectors

"""Job handlers for documents module.

Registered handlers:
  documents.extract  — extract text from uploaded file
  checks.run         — run deterministic rule checks
  advice.generate    — generate AI advice
"""
import hashlib
import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.utils import make_uuid
from jobs.registry import register

logger = logging.getLogger(__name__)

PRIVATE_ROOT = Path(settings.PRIVATE_FILES_ROOT)


def _handle_extract(job):
    """Extract text from document and store units."""
    from documents.checker import extract_units
    from documents.models import DocumentVersion, Extraction

    payload = job.payload_json
    version_id = payload["version_id"]

    try:
        version = DocumentVersion.objects.get(pk=version_id)
    except DocumentVersion.DoesNotExist:
        raise RuntimeError(f"Version {version_id} not found")

    extraction = Extraction.objects.filter(version=version).first()
    if not extraction:
        extraction = Extraction.objects.create(
            version=version, parser_version="v1", state=Extraction.STATE_QUEUED
        )

    extraction.state = Extraction.STATE_RUNNING
    extraction.save(update_fields=["state"])

    path = PRIVATE_ROOT / version.storage_key

    try:
        result = extract_units(path)
        with transaction.atomic():
            extraction.state = Extraction.STATE_SUCCEEDED
            extraction.units_json = result["units"]
            extraction.limitations_json = result["limitations"]
            extraction.error_code = None
            extraction.save()
        return extraction.id
    except ValueError as exc:
        code = str(exc)
        unsupported = code in ("text_layer_unavailable",)
        with transaction.atomic():
            extraction.state = Extraction.STATE_UNSUPPORTED if unsupported else Extraction.STATE_FAILED
            extraction.error_code = code
            extraction.units_json = None
            extraction.save()
        if unsupported:
            return extraction.id  # Not a fatal job error
        raise RuntimeError(code) from exc


def _handle_check(job):
    """Run deterministic checks and persist results."""
    from documents.checker import extract_units, run_checks
    from documents.models import CheckRun, DocumentVersion, Extraction, Finding, RuleResult

    payload = job.payload_json
    run_id = payload["run_id"]
    version_id = payload["version_id"]

    try:
        run = CheckRun.objects.get(pk=run_id)
        version = DocumentVersion.objects.get(pk=version_id)
    except (CheckRun.DoesNotExist, DocumentVersion.DoesNotExist) as exc:
        raise RuntimeError(str(exc)) from exc

    run.state = CheckRun.STATE_RUNNING
    run.save(update_fields=["state"])

    path = PRIVATE_ROOT / version.storage_key

    try:
        # Use existing extraction if available
        extraction_obj = Extraction.objects.filter(
            version=version, state=Extraction.STATE_SUCCEEDED
        ).first()
        if extraction_obj and extraction_obj.units_json:
            extraction = {"format": version.format, "units": extraction_obj.units_json}
        else:
            extraction = extract_units(path)

        ruleset = run.rules_snapshot
        check_result = run_checks(extraction, ruleset)

        with transaction.atomic():
            # Persist rule results
            for rule_id, res in check_result["rule_results"].items():
                RuleResult.objects.create(
                    id=make_uuid(),
                    run=run,
                    rule_id=rule_id,
                    outcome=res["outcome"],
                    explanation=res["explanation"],
                    scope_json={},
                )
            # Persist findings
            for f in check_result["findings"]:
                Finding.objects.create(
                    id=make_uuid(),
                    run=run,
                    rule_id=f["rule_id"],
                    severity=f["severity"],
                    location_json=f["location"],
                    evidence=f["evidence"][:500],
                    explanation=f["explanation"],
                    action=f["action"],
                )
            run.state = CheckRun.STATE_SUCCEEDED
            run.completed_at = timezone.now()
            run.save(update_fields=["state", "completed_at"])

        return run_id

    except Exception as exc:
        run.state = CheckRun.STATE_FAILED
        run.completed_at = timezone.now()
        run.save(update_fields=["state", "completed_at"])
        raise RuntimeError(str(exc)) from exc


def _handle_advice(job):
    """Generate AI advice and persist the result."""
    from documents.checker import extract_units, select_advice_units
    from documents.models import AdviceRun, CheckRun, DocumentVersion, Extraction, Finding, RuleResult

    payload = job.payload_json
    advice_run_id = payload["advice_run_id"]
    version_id = payload["version_id"]

    try:
        advice_run = AdviceRun.objects.get(pk=advice_run_id)
        version = DocumentVersion.objects.get(pk=version_id)
    except (AdviceRun.DoesNotExist, DocumentVersion.DoesNotExist) as exc:
        raise RuntimeError(str(exc)) from exc

    advice_run.state = AdviceRun.STATE_RUNNING
    advice_run.save(update_fields=["state"])

    try:
        path = PRIVATE_ROOT / version.storage_key

        # Get or compute extraction
        extraction_obj = Extraction.objects.filter(
            version=version, state=Extraction.STATE_SUCCEEDED
        ).first()
        if extraction_obj and extraction_obj.units_json:
            units = extraction_obj.units_json
        else:
            result = extract_units(path)
            units = result["units"]

        # Select bounded units
        selected, omitted = select_advice_units(units)

        # Get relevant findings from latest successful check
        latest_run = CheckRun.objects.filter(
            version=version, state=CheckRun.STATE_SUCCEEDED
        ).order_by("-created_at").first()
        findings_data = []
        if latest_run:
            for f in Finding.objects.filter(run=latest_run):
                findings_data.append({
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "location": f.location_json,
                    "evidence": f.evidence,
                    "explanation": f.explanation,
                })

        # Get saved project sources
        from projects.models import ProjectSource
        from recommendations.models import Source
        saved_ids = list(
            ProjectSource.objects.filter(project_id=version.project_id).values_list("source_id", flat=True)
        )
        sources = list(
            Source.objects.filter(id__in=saved_ids, synthetic=False).values(
                "id", "title", "authors", "year", "annotation", "url"
            )
        )

        # Call AI gateway
        from recommendations.gateway import AIError, generate_advice

        response = generate_advice(
            units=[{"id": u["id"], "text": u["text"]} for u in selected],
            findings=findings_data,
            sources=sources,
        )
        batch = response.output_parsed
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            }

        with transaction.atomic():
            advice_run.state = AdviceRun.STATE_SUCCEEDED
            advice_run.response_json = {
                "suggestions": [s.model_dump() for s in batch.suggestions],
                "limitations": batch.limitations,
            }
            advice_run.response_id = getattr(response, "id", None)
            advice_run.usage_json = usage
            advice_run.coverage_json = {
                "selected_unit_ids": [u["id"] for u in selected],
                "omitted_unit_ids": omitted,
            }
            advice_run.completed_at = timezone.now()
            advice_run.save()

        return advice_run_id

    except Exception as exc:
        error_code = getattr(exc, "code", str(exc))[:100]
        advice_run.state = AdviceRun.STATE_FAILED
        advice_run.completed_at = timezone.now()
        advice_run.save(update_fields=["state", "completed_at"])
        raise RuntimeError(error_code) from exc


register("documents.extract", _handle_extract)
register("checks.run", _handle_check)
register("advice.generate", _handle_advice)

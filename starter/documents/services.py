"""Documents domain services: upload, extraction, checks, advice."""
import hashlib
import logging
import os
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.errors import Conflict, Forbidden, NotFound, UnsupportedDocument, ValidationError
from core.utils import fingerprint, make_uuid

logger = logging.getLogger(__name__)

PRIVATE_ROOT = Path(settings.PRIVATE_FILES_ROOT)


def _private_path(storage_key: str) -> Path:
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    return PRIVATE_ROOT / storage_key


def accept_upload(actor, project_id, file_obj, original_filename: str, idempotency_key: str | None = None):
    """Validate and store a file, create DocumentVersion and queue extraction.

    Returns (DocumentVersion, Job).
    """
    from documents.models import DocumentVersion, Extraction
    from governance.models import Job
    from jobs.engine import enqueue
    from projects.services import authorize_project

    project = authorize_project(actor, project_id, "upload")

    # Stream to staging
    suffix = Path(original_filename).suffix.lower()
    staging_path = PRIVATE_ROOT / f"staging_{uuid.uuid4()}{suffix}"
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        with open(staging_path, "wb") as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

        # Validate container
        from documents.checker import validate_container
        try:
            fmt = validate_container(staging_path)
        except ValueError as exc:
            raise UnsupportedDocument(str(exc)) from exc

        # Check size
        size = staging_path.stat().st_size

        # Compute SHA-256
        sha256 = hashlib.sha256(staging_path.read_bytes()).hexdigest()

        # Idempotency check: same bytes + project
        if idempotency_key:
            existing = DocumentVersion.objects.filter(
                project_id=project_id, sha256=sha256
            ).first()
            if existing:
                # Return existing version without creating a new one
                existing_job = Job.objects.filter(
                    project_id=project_id,
                    kind="documents.extract",
                    state__in=[Job.STATE_QUEUED, Job.STATE_RUNNING],
                ).first()
                return existing, existing_job

        # Move to permanent storage
        storage_key = f"docs/{project_id}/{uuid.uuid4()}{suffix}"
        dest_path = PRIVATE_ROOT / storage_key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_path), str(dest_path))

        # Allocate version number atomically
        with transaction.atomic():
            # Re-authorize inside transaction
            project = authorize_project(actor, project_id, "upload")
            number = DocumentVersion.objects.filter(project_id=project_id).count() + 1
            version = DocumentVersion.objects.create(
                project_id=project_id,
                number=number,
                author=actor,
                original_filename=original_filename,
                storage_key=storage_key,
                sha256=sha256,
                size_bytes=size,
                format=fmt,
            )

        # Queue extraction
        fp = fingerprint(version.id, "extract", PRIVATE_ROOT / storage_key)
        job, _ = enqueue(
            project_id=project_id,
            creator_id=actor.id,
            kind="documents.extract",
            fingerprint=fp,
            payload={"version_id": version.id},
        )

        # Create extraction placeholder
        Extraction.objects.create(
            version=version,
            parser_version="v1",
            state=Extraction.STATE_QUEUED,
        )

        from governance.services import audit
        audit(actor, "document.uploaded", "DocumentVersion", version.id,
              project_id=project_id, format=fmt, size=str(size))

        return version, job

    except UnsupportedDocument:
        if staging_path.exists():
            staging_path.unlink(missing_ok=True)
        raise
    except Exception:
        if staging_path.exists():
            staging_path.unlink(missing_ok=True)
        raise


def download_version(actor, version_id):
    """Return (path, filename) for an authorized file download."""
    from documents.models import DocumentVersion
    from projects.services import authorize_project

    try:
        version = DocumentVersion.objects.get(pk=version_id)
    except DocumentVersion.DoesNotExist:
        raise NotFound("version", version_id)

    authorize_project(actor, version.project_id, "view")
    path = _private_path(version.storage_key)
    if not path.exists():
        raise NotFound("file", version_id)
    return path, version.original_filename


def queue_check(actor, project_id, version_id, idempotency_key: str | None = None):
    """Queue a check run for a document version."""
    from documents.models import CheckRun, DocumentVersion
    from governance.services import audit, get_active_ruleset
    from jobs.engine import enqueue
    from projects.services import authorize_project

    authorize_project(actor, project_id, "check")
    try:
        version = DocumentVersion.objects.get(pk=version_id, project_id=project_id)
    except DocumentVersion.DoesNotExist:
        raise NotFound("version", version_id)

    ruleset = get_active_ruleset()
    if not ruleset:
        raise Conflict("no_ruleset")

    rules_hash = hashlib.sha256(
        str(ruleset.rules_json).encode()
    ).hexdigest()

    # Create CheckRun record
    run = CheckRun.objects.create(
        version=version,
        requirement_set_id=ruleset.id,
        rules_snapshot=ruleset.rules_json,
        rules_hash=rules_hash,
        parser_version="v1",
        state=CheckRun.STATE_QUEUED,
    )

    fp = fingerprint(version_id, "check", rules_hash)
    job, _ = enqueue(
        project_id=project_id,
        creator_id=actor.id,
        kind="checks.run",
        fingerprint=fp,
        payload={"run_id": run.id, "version_id": version_id},
    )

    run.job_id = job.id  # informational only
    return run, job


def queue_advice(actor, project_id, version_id, idempotency_key: str | None = None):
    """Queue an AI advice run for a document version."""
    from documents.models import AdviceRun, DocumentVersion
    from jobs.engine import enqueue
    from projects.services import authorize_project
    from recommendations.models import Source

    authorize_project(actor, project_id, "advice")
    try:
        version = DocumentVersion.objects.get(pk=version_id, project_id=project_id)
    except DocumentVersion.DoesNotExist:
        raise NotFound("version", version_id)

    # Saved project sources
    from projects.models import ProjectSource
    saved_ids = list(
        ProjectSource.objects.filter(project_id=project_id).values_list("source_id", flat=True)
    )

    model = settings.OPENAI_MODEL
    input_hash = fingerprint(version_id, "advice", model, str(sorted(saved_ids)))

    advice_run = AdviceRun.objects.create(
        version=version,
        model=model,
        origin=AdviceRun.ORIGIN_LIVE,
        input_hash=input_hash,
        source_ids=saved_ids,
        coverage_json={},
        state=AdviceRun.STATE_QUEUED,
    )

    fp = fingerprint(version_id, "advice", model)
    job, _ = enqueue(
        project_id=project_id,
        creator_id=actor.id,
        kind="advice.generate",
        fingerprint=fp,
        payload={"advice_run_id": advice_run.id, "version_id": version_id},
    )

    return advice_run, job

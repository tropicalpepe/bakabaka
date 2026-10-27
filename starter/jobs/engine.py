"""Durable job engine for the sequential background worker."""
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

LEASE_SECONDS = 90
MAX_ATTEMPTS = 3


def enqueue(project_id, creator_id, kind, fingerprint, payload):
    """Create a queued Job, or return existing active job for same fingerprint.

    Returns (job, created: bool).
    """
    from governance.models import Job

    existing = Job.objects.filter(
        project_id=project_id,
        kind=kind,
        fingerprint=fingerprint,
        state__in=[Job.STATE_QUEUED, Job.STATE_RUNNING],
    ).first()
    if existing:
        return existing, False

    job = Job.objects.create(
        project_id=project_id,
        creator_id=creator_id,
        kind=kind,
        fingerprint=fingerprint,
        payload_json=payload,
        state=Job.STATE_QUEUED,
    )
    return job, True


def claim_next(kinds):
    """Atomically claim one queued job for processing.

    Returns a Job in RUNNING state, or None if nothing queued.
    """
    from governance.models import Job

    fail_exhausted()

    job = (
        Job.objects.filter(state=Job.STATE_QUEUED, kind__in=kinds)
        .order_by("created_at")
        .first()
    )
    if not job:
        return None

    token = str(uuid.uuid4())
    expires = timezone.now() + timedelta(seconds=LEASE_SECONDS)
    updated = Job.objects.filter(pk=job.pk, state=Job.STATE_QUEUED).update(
        state=Job.STATE_RUNNING,
        lease_token=token,
        lease_expires_at=expires,
        attempts=F("attempts") + 1,
    )
    if not updated:
        return None  # Another worker claimed it first
    job.refresh_from_db()
    return job


def heartbeat(job_id, lease_token):
    """Extend the lease. Returns True if lease was still valid."""
    from governance.models import Job

    expires = timezone.now() + timedelta(seconds=LEASE_SECONDS)
    return bool(
        Job.objects.filter(
            pk=job_id, state=Job.STATE_RUNNING, lease_token=lease_token
        ).update(lease_expires_at=expires)
    )


def finish(job_id, lease_token, result_id=None, error_code=None):
    """Commit job result atomically. Returns True if lease was valid."""
    from governance.models import Job

    state = Job.STATE_SUCCEEDED if result_id else Job.STATE_FAILED
    with transaction.atomic():
        updated = Job.objects.filter(
            pk=job_id, state=Job.STATE_RUNNING, lease_token=lease_token
        ).update(
            state=state,
            result_id=result_id,
            error_code=error_code,
            lease_token=None,
            lease_expires_at=None,
        )
    return bool(updated)


def fail_exhausted():
    """Mark expired running jobs that exceeded max attempts as failed."""
    from governance.models import Job

    now = timezone.now()
    Job.objects.filter(
        state=Job.STATE_RUNNING,
        lease_expires_at__lt=now,
        attempts__gte=MAX_ATTEMPTS,
    ).update(state=Job.STATE_FAILED, error_code="max_attempts_exceeded")

    # Requeue expired jobs that still have attempts remaining
    Job.objects.filter(
        state=Job.STATE_RUNNING,
        lease_expires_at__lt=now,
        attempts__lt=MAX_ATTEMPTS,
    ).update(state=Job.STATE_QUEUED, lease_token=None, lease_expires_at=None)

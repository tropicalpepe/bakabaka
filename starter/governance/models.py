from django.conf import settings
from django.db import models

from core.utils import make_uuid


class RequirementSet(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    key = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    programme = models.CharField(max_length=200)
    rules_json = models.JSONField()
    sha256 = models.CharField(max_length=64)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("key", "version")]

    def save(self, *args, **kwargs):
        if self.pk and RequirementSet.objects.filter(pk=self.pk).exists():
            raise ValueError("Published rule sets are immutable")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.key} v{self.version}"


class Job(models.Model):
    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_SUCCEEDED = "succeeded"
    STATE_FAILED = "failed"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_QUEUED, "Queued"),
        (STATE_RUNNING, "Running"),
        (STATE_SUCCEEDED, "Succeeded"),
        (STATE_FAILED, "Failed"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    project_id = models.CharField(max_length=36, db_index=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_jobs",
    )
    kind = models.CharField(max_length=50)
    fingerprint = models.CharField(max_length=64)
    payload_json = models.JSONField()
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.CharField(max_length=36, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    result_id = models.CharField(max_length=36, null=True, blank=True)
    error_code = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "kind", "created_at"]),
            models.Index(fields=["project_id", "state"]),
        ]

    def __str__(self):
        return f"{self.kind}:{self.id[:8]} ({self.state})"


class AuditEvent(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=50)
    object_id = models.CharField(max_length=36)
    result = models.CharField(max_length=20, default="ok")
    metadata_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["actor", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action}:{self.object_type}:{self.object_id[:8]}"

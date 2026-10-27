from django.conf import settings
from django.db import models

from core.utils import make_uuid


class SupervisorAssignment(models.Model):
    """Supervision request and its lifecycle."""

    STATE_REQUESTED = "requested"
    STATE_ACCEPTED = "accepted"
    STATE_DECLINED = "declined"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_REQUESTED, "Pieprasījums nosūtīts"),
        (STATE_ACCEPTED, "Sadarbība apstiprināta"),
        (STATE_DECLINED, "Pieprasījums noraidīts"),
        (STATE_CANCELLED, "Pieprasījums atcelts"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    project_id = models.CharField(max_length=36, db_index=True)
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="supervision_assignments",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_assignments",
    )
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_REQUESTED)
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(default="", blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Assignment({self.project_id[:8]}, {self.supervisor_id}, {self.state})"


class Comment(models.Model):
    """Supervisor or student comment on a document version or finding."""

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="comments",
    )
    # Exactly one of version_id or finding_id must be set (enforced in save)
    version_id = models.CharField(max_length=36, null=True, blank=True, db_index=True)
    finding_id = models.CharField(max_length=36, null=True, blank=True, db_index=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if bool(self.version_id) == bool(self.finding_id):
            raise ValueError("Exactly one of version_id or finding_id must be set")
        if not self.text.strip():
            raise ValueError("Comment text cannot be empty")
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.version_id or self.finding_id
        return f"Comment({self.actor_id}, {target[:8] if target else '?'})"

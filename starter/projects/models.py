"""Core project domain models."""
from django.conf import settings
from django.db import IntegrityError, models

from core.utils import make_uuid


class Project(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="projects",
    )
    requirement_set = models.ForeignKey(
        "governance.RequirementSet",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=300)
    idea = models.TextField()
    keywords = models.JSONField(default=list)
    context_revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class TopicDraft(models.Model):
    """Editable draft — one per project, optimistically locked by revision."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="topic_draft",
    )
    title = models.CharField(max_length=300, default="")
    hypothesis = models.TextField(default="")
    rationale = models.TextField(default="")
    source_ids = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_drafts",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Draft({self.project_id[:8]})"


class TopicGeneration(models.Model):
    """Record of an AI topic generation attempt."""

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="topic_generations",
    )
    context_fingerprint = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=20, default="topic-v1")
    model = models.CharField(max_length=100)
    origin = models.CharField(max_length=30)  # 'openai_live' | 'synthetic_fixture'
    proposals_json = models.JSONField()
    source_ids = models.JSONField(default=list)
    limitations = models.TextField(default="")
    usage_json = models.JSONField(null=True, blank=True)
    response_id = models.CharField(max_length=200, null=True, blank=True)
    is_stale = models.BooleanField(default=False)
    job = models.ForeignKey(
        "governance.Job",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="topic_generation",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Gen({self.id[:8]}, {self.origin})"


class TopicSubmission(models.Model):
    """Immutable snapshot of a draft at submission time."""

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    assignment = models.ForeignKey(
        "supervision.SupervisorAssignment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=300)
    hypothesis = models.TextField()
    rationale = models.TextField()
    sources_snapshot = models.JSONField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_topics",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "number")]
        ordering = ["-number"]

    def save(self, *args, **kwargs):
        if self.pk and TopicSubmission.objects.filter(pk=self.pk).exists():
            raise IntegrityError("Topic submission content is immutable")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Submission({self.project_id[:8]} #{self.number})"

    def display_state(self):
        """Derived display state for the submission."""
        try:
            decision = self.decision
            return decision.decision  # 'approved' | 'changes_requested' | 'rejected'
        except TopicDecision.DoesNotExist:
            pass
        # Check if a newer submission exists
        if TopicSubmission.objects.filter(
            project=self.project, number__gt=self.number
        ).exists():
            return "superseded"
        return "submitted"


class TopicDecision(models.Model):
    """Immutable supervisor decision on a topic submission."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    DECISION_CHOICES = [
        (APPROVED, "Apstiprināta"),
        (CHANGES_REQUESTED, "Nepieciešami labojumi"),
        (REJECTED, "Noraidīta"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    submission = models.OneToOneField(
        TopicSubmission,
        on_delete=models.CASCADE,
        related_name="decision",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="topic_decisions",
    )
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    comment = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and TopicDecision.objects.filter(pk=self.pk).exists():
            raise IntegrityError("Topic decision is immutable")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Decision({self.decision}, submission={self.submission_id[:8]})"


class ProjectSource(models.Model):
    """Saved source link for a project. Source content lives in recommendations.Source."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="project_sources",
    )
    source_id = models.CharField(max_length=50)
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="saved_sources",
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project", "source_id")]
        ordering = ["-saved_at"]

    def __str__(self):
        return f"ProjectSource({self.project_id[:8]}, {self.source_id})"

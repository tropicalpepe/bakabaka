from django.conf import settings
from django.db import IntegrityError, models

from core.utils import make_uuid


class DocumentVersion(models.Model):
    """Immutable uploaded document version."""

    FORMAT_PDF = "pdf"
    FORMAT_DOCX = "docx"
    FORMAT_CHOICES = [(FORMAT_PDF, "PDF"), (FORMAT_DOCX, "DOCX")]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    project_id = models.CharField(max_length=36, db_index=True)
    number = models.PositiveIntegerField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_versions",
    )
    original_filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=255, unique=True)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("project_id", "number")]
        ordering = ["-number"]

    def save(self, *args, **kwargs):
        if self.pk and DocumentVersion.objects.filter(pk=self.pk).exists():
            raise IntegrityError("Document version is immutable")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"v{self.number} {self.original_filename}"


class Extraction(models.Model):
    """Text extraction from a document version."""

    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_SUCCEEDED = "succeeded"
    STATE_FAILED = "failed"
    STATE_UNSUPPORTED = "unsupported"
    STATE_CHOICES = [
        (STATE_QUEUED, "Gaida"),
        (STATE_RUNNING, "Apstrādā"),
        (STATE_SUCCEEDED, "Veiksmīgi"),
        (STATE_FAILED, "Kļūda"),
        (STATE_UNSUPPORTED, "Neatbalstīts"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    version = models.OneToOneField(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name="extraction",
    )
    parser_version = models.CharField(max_length=50, default="v1")
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_QUEUED)
    units_json = models.JSONField(null=True, blank=True)
    limitations_json = models.JSONField(default=list)
    error_code = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Extraction(v{self.version.number}, {self.state})"


class CheckRun(models.Model):
    """One run of all configured rules against a document version."""

    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_SUCCEEDED = "succeeded"
    STATE_FAILED = "failed"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_QUEUED, "Gaida"),
        (STATE_RUNNING, "Pārbauda"),
        (STATE_SUCCEEDED, "Pabeigta"),
        (STATE_FAILED, "Kļūda"),
        (STATE_CANCELLED, "Atcelta"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name="check_runs",
    )
    requirement_set_id = models.CharField(max_length=36)
    rules_snapshot = models.JSONField()
    rules_hash = models.CharField(max_length=64)
    parser_version = models.CharField(max_length=50)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CheckRun({self.version_id[:8]}, {self.state})"


class RuleResult(models.Model):
    """Result of one rule in a check run. Immutable once the run succeeds."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_ASSESSED = "not_assessed"
    OUTCOME_CHOICES = [
        (PASSED, "Izpildīts"),
        (FAILED, "Neizpildīts"),
        (NOT_ASSESSED, "Nav izvērtēts"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    run = models.ForeignKey(CheckRun, on_delete=models.CASCADE, related_name="rule_results")
    rule_id = models.CharField(max_length=50)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES)
    explanation = models.TextField()
    scope_json = models.JSONField(default=dict)

    class Meta:
        unique_together = [("run", "rule_id")]

    def __str__(self):
        return f"RuleResult({self.rule_id}, {self.outcome})"


class Finding(models.Model):
    """An individual problem found by a rule. Immutable."""

    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_ERROR = "error"
    SEVERITY_CHOICES = [
        (SEVERITY_INFO, "Informācija"),
        (SEVERITY_WARNING, "Brīdinājums"),
        (SEVERITY_ERROR, "Kļūda"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    run = models.ForeignKey(CheckRun, on_delete=models.CASCADE, related_name="findings")
    rule_id = models.CharField(max_length=50)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    location_json = models.JSONField()
    evidence = models.TextField()
    explanation = models.TextField()
    action = models.TextField()

    def save(self, *args, **kwargs):
        if self.pk and Finding.objects.filter(pk=self.pk).exists():
            raise IntegrityError("Finding evidence is immutable")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Finding({self.rule_id}, {self.severity})"


class FindingResolution(models.Model):
    """Student's resolution claim for a finding. Does not change the finding."""

    STATE_OPEN = "open"
    STATE_CLAIMED = "claimed_fixed"
    STATE_CHOICES = [
        (STATE_OPEN, "Atklāts"),
        (STATE_CLAIMED, "Students atzīmējis kā izlabotu"),
    ]

    finding = models.OneToOneField(Finding, on_delete=models.CASCADE, related_name="resolution", primary_key=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_OPEN)
    note = models.TextField(default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Resolution({self.finding_id[:8]}, {self.state})"


class AdviceRun(models.Model):
    """AI advisory feedback run. Immutable once succeeded."""

    ORIGIN_LIVE = "openai_live"
    ORIGIN_FIXTURE = "synthetic_fixture"
    ORIGIN_CHOICES = [
        (ORIGIN_LIVE, "OpenAI (tiešraide)"),
        (ORIGIN_FIXTURE, "Demonstrācijas piemērs"),
    ]
    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_SUCCEEDED = "succeeded"
    STATE_FAILED = "failed"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_QUEUED, "Gaida"),
        (STATE_RUNNING, "Ģenerē"),
        (STATE_SUCCEEDED, "Pabeigts"),
        (STATE_FAILED, "Kļūda"),
        (STATE_CANCELLED, "Atcelts"),
    ]

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name="advice_runs",
    )
    model = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=20, default="advice-v1")
    schema_version = models.CharField(max_length=20, default="1")
    origin = models.CharField(max_length=30, choices=ORIGIN_CHOICES)
    input_hash = models.CharField(max_length=64)
    source_ids = models.JSONField(default=list)
    coverage_json = models.JSONField(default=dict)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=STATE_QUEUED)
    response_json = models.JSONField(null=True, blank=True)
    response_id = models.CharField(max_length=200, null=True, blank=True)
    usage_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AdviceRun({self.version_id[:8]}, {self.origin}, {self.state})"

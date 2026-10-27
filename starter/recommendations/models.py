from django.db import models

from core.utils import make_uuid


class Source(models.Model):
    """Scholarly or synthetic source record from the packaged corpus."""

    id = models.CharField(max_length=50, primary_key=True)  # ARXIV-xxxx or DEMO-LV-xxx
    title = models.CharField(max_length=500)
    authors = models.JSONField(default=list)  # List of author strings
    year = models.IntegerField()
    url = models.URLField(max_length=500)
    repository = models.CharField(max_length=100)
    language = models.CharField(max_length=20)
    document_type = models.CharField(max_length=50)
    annotation = models.TextField()
    evidence_level = models.CharField(max_length=50, default="metadata_annotation")
    synthetic = models.BooleanField(default=False)
    provenance = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ["-year", "title"]

    def __str__(self):
        return f"{self.id}: {self.title[:60]}"


class SourceEmbedding(models.Model):
    """Cached embedding vector for a source."""

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="embeddings")
    model = models.CharField(max_length=100)
    dimensions = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    vector_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("source", "model", "dimensions", "content_hash")]

    def __str__(self):
        return f"Embedding({self.source_id}, {self.model}, {self.dimensions}d)"


class QueryEmbedding(models.Model):
    """Cached embedding for a search query."""

    id = models.CharField(max_length=36, primary_key=True, default=make_uuid)
    query_hash = models.CharField(max_length=64, unique=True)
    model = models.CharField(max_length=100)
    dimensions = models.PositiveIntegerField()
    vector_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"QueryEmbedding({self.query_hash[:12]})"

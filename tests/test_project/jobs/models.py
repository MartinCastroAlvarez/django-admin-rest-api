from django.db import models


class Job(models.Model):
    """A job whose admin mixes a stock change form (Path A) with a custom
    request-driven view (Path B) — see ``admin.JobAdmin``."""

    name = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, default="idle")

    def __str__(self) -> str:
        return self.name

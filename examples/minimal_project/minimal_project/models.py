"""A trivial `Note` model so the example admin has something to register."""

from __future__ import annotations

from django.db import models


class Note(models.Model):
    """One-line note + archive flag + priority. Just enough for the two
    example actions to do something visible."""

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    priority = models.IntegerField(default=0)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-priority", "-created_at")

    def __str__(self) -> str:
        return self.title

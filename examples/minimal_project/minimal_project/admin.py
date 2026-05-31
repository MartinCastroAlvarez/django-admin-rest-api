"""Example ModelAdmin with one batch and one detail action.

Both actions follow Django's standard ``@admin.action`` decorator —
there is no package-specific API. The package classifies them by
signature:

- ``reprocess_batch(self, request, queryset)`` → ``target=batch``
  (the SPA renders this on the changelist with multi-select).
- ``reprocess_one(self, request, obj_id: str)`` → ``target=detail``
  (the SPA renders this on the single-object detail page).
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet

from minimal_project.models import Note


@admin.action(description="Mark archived")
def mark_archived(modeladmin, request, queryset: QuerySet) -> None:  # noqa: ARG001
    """Batch shape: takes a queryset. Renders on the changelist."""
    queryset.update(archived=True)


@admin.action(description="Bump priority")
def bump_priority(modeladmin, request, obj_id: str) -> None:  # noqa: ARG001
    """Detail shape: takes a single object id. Renders on the detail page."""
    Note.objects.filter(pk=obj_id).update(priority=models.F("priority") + 1)


# Re-import models here so the F() expression in bump_priority resolves
# at call-time without a circular import at module-import time.
from django.db import models  # noqa: E402  (intentional after the action defs)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "archived", "created_at")
    list_filter = ("archived",)
    search_fields = ("title", "body")
    actions = [mark_archived, bump_priority]

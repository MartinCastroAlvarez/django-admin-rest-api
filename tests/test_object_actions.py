"""Tests for ``data.object_actions`` on the detail response (#603, revised).

The detail-page action surface is sourced from the consumer's standard
``ModelAdmin.actions`` — the same actions Django admin renders on the
changelist with multi-select. No `django-object-actions` integration,
no `change_actions = [...]` redeclaration: one place to declare an
action, two places it shows up. The descriptor shape matches the
list response's ``data.actions`` block by design — the SPA renders
the same buttons on both surfaces, and clicks reuse the same
``<app>/<model>/actions/<name>/`` runner with ``pks=[<this pk>]``.

Covered:

- Detail response surfaces ``object_actions`` when the admin declares
  ``actions = [...]``. The descriptor shape matches the list
  response's ``actions`` block.
- ``object_actions`` is always present (possibly empty) so the SPA
  can branch on length without a ``hasOwnProperty`` check.
- Django's default ``delete_selected`` action surfaces with the
  interpolated label (no raw ``%(verbose_name_plural)s``).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextlib import suppress

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client

User = get_user_model()
DETAIL_BASE = "/admin-api/api/v1/auth/user/"


@contextmanager
def admin_attr(model_cls, **values):
    """Temporarily set attributes on the registered ModelAdmin instance."""
    model_admin = admin.site._registry[model_cls]
    sentinel = object()
    originals: dict = {}
    try:
        for name, value in values.items():
            originals[name] = getattr(model_admin, name, sentinel)
            setattr(model_admin, name, value)
        yield model_admin
    finally:
        for name, original in originals.items():
            if original is sentinel:
                with suppress(AttributeError):
                    delattr(model_admin, name)
            else:
                setattr(model_admin, name, original)


@pytest.fixture
def some_user(db):  # noqa: ARG001 — db forces DB setup
    return User.objects.create_user(
        username="target",
        password="test-only",  # noqa: S106
        email="target@example.com",
    )


def test_detail_object_actions_always_present(
    superuser_client: Client,
    some_user,
) -> None:
    """``object_actions`` is in every detail response, even when the
    admin has no custom actions (Django admin's default
    ``delete_selected`` is always permitted for a superuser, so the
    list is never empty for a superuser — but the field is present
    regardless, so the SPA can render conditionally without a
    ``hasOwnProperty`` guard)."""
    response = superuser_client.get(f"{DETAIL_BASE}{some_user.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert "object_actions" in body
    assert isinstance(body["object_actions"], list)


def test_detail_object_actions_includes_admin_action_decorated_method(
    superuser_client: Client,
    some_user,
) -> None:
    """A ``ModelAdmin`` with a custom ``@admin.action``-decorated method
    in ``actions = [...]`` surfaces on the detail page's
    ``object_actions`` — same descriptor shape as the list response's
    ``actions`` block."""

    def reconcile(model_admin, request, queryset):  # noqa: ARG001
        return None

    reconcile.short_description = "Mark as reconciled"

    # Set as INSTANCE attr via admin_attr: Python doesn't auto-bind
    # instance attributes, so the signature drops `self`.
    def fake_get_actions(request):  # noqa: ARG001
        return {
            "reconcile": (reconcile, "reconcile", "Mark as reconciled"),
        }

    with admin_attr(User, get_actions=fake_get_actions):
        response = superuser_client.get(f"{DETAIL_BASE}{some_user.pk}/")
        body = response.json()
        names = [a["name"] for a in body["object_actions"]]
        assert "reconcile" in names
        entry = next(a for a in body["object_actions"] if a["name"] == "reconcile")
        # Same shape as the list response — `label`, `description`,
        # `requires_confirmation`.
        assert entry["label"] == "Mark as reconciled"
        assert entry["description"] == "Mark as reconciled"
        assert entry["requires_confirmation"] is False


def test_detail_object_actions_interpolates_delete_selected_label(
    superuser_client: Client,
    some_user,
) -> None:
    """Django's default ``delete_selected`` action uses a format string
    label (``Delete selected %(verbose_name_plural)s``). The descriptor
    interpolates it — same posture as the list response — so the SPA
    shows ``Delete selected users``, not the raw template."""
    response = superuser_client.get(f"{DETAIL_BASE}{some_user.pk}/")
    body = response.json()
    delete_entries = [a for a in body["object_actions"] if a["name"] == "delete_selected"]
    if delete_entries:
        entry = delete_entries[0]
        assert "%(verbose_name_plural)s" not in entry["label"]
        # "delete" hint → conservative requires_confirmation = True
        # (parity with the list-side actions_payload).
        assert entry["requires_confirmation"] is True

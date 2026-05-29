"""Tests for the per-object action runner + descriptor (#603).

The companion descriptor list is exposed on the detail response as
``data.object_actions``; the POST runner lives at
``<app>/<model>/<pk>/action/<name>/``. Both are driven by
``ModelAdmin.get_change_actions`` (the ``django-object-actions``
extension point) — duck-typed, so this test stubs that method
directly on the ``UserAdmin`` rather than depending on the third-party
package.

Covered:

- Detail response surfaces ``object_actions`` when the admin exposes
  ``get_change_actions`` returning a non-empty list.
- Detail response returns an empty list when the admin doesn't expose
  the method (the 99% case — no behavioural change for those admins).
- POST runner: anonymous → 403, non-staff → 403, unknown action name
  → 404, permitted action runs and returns the JSON envelope.
- Action callable that raises an `HttpResponseRedirect` surfaces
  ``redirect`` in the response body.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextlib import suppress

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
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


# --------------------------------------------------------------------------- #
# Detail descriptor                                                           #
# --------------------------------------------------------------------------- #
def test_detail_object_actions_empty_when_admin_has_no_change_actions(
    superuser_client: Client, some_user,
) -> None:
    """The 99% case: admins that don't use django-object-actions return
    an empty list. No behaviour change for them (#603 backwards-compat)."""
    response = superuser_client.get(f"{DETAIL_BASE}{some_user.pk}/")
    assert response.status_code == 200
    body = response.json()
    assert body.get("object_actions") == []


def test_detail_object_actions_surfaces_change_actions(
    superuser_client: Client, some_user,
) -> None:
    """When the admin exposes `get_change_actions`, each name is
    serialised as `{name, label, description}` for the SPA to render
    (#603)."""

    # Set as INSTANCE attrs via admin_attr: Python doesn't auto-bind
    # instance attributes, so the signature drops `self`.
    def my_action(request, obj):  # noqa: ARG001
        return None

    my_action.label = "Run the thing"
    my_action.short_description = "Runs the thing on this user"

    def get_change_actions(request, object_id, form_url):  # noqa: ARG001
        return ["my_action"]

    with admin_attr(User, my_action=my_action, get_change_actions=get_change_actions):
        response = superuser_client.get(f"{DETAIL_BASE}{some_user.pk}/")
        body = response.json()
        assert body["object_actions"] == [
            {
                "name": "my_action",
                "label": "Run the thing",
                "description": "Runs the thing on this user",
            }
        ]


# --------------------------------------------------------------------------- #
# Runner                                                                      #
# --------------------------------------------------------------------------- #
def test_run_object_action_anonymous_403(anon_client: Client, some_user) -> None:
    """Auth gate matches the rest of the surface (`SECURITY.md` §3
    Rule 1) — anonymous gets 403, never reaches the admin."""
    response = anon_client.post(f"{DETAIL_BASE}{some_user.pk}/action/my_action/")
    assert response.status_code == 403


def test_run_object_action_non_staff_403(user_client: Client, some_user) -> None:
    """Non-staff gets 403 even with a session."""
    response = user_client.post(f"{DETAIL_BASE}{some_user.pk}/action/my_action/")
    assert response.status_code == 403


def test_run_object_action_unknown_name_404(
    superuser_client: Client, some_user,
) -> None:
    """Unknown action name → 404 (never trust the URL — the admin's
    `get_change_actions` is the source of truth)."""

    def get_change_actions(request, object_id, form_url):  # noqa: ARG001
        return ["a_known_one"]

    with admin_attr(User, get_change_actions=get_change_actions):
        response = superuser_client.post(
            f"{DETAIL_BASE}{some_user.pk}/action/something_else/",
        )
        assert response.status_code == 404


def test_run_object_action_runs_and_returns_envelope(
    superuser_client: Client, some_user,
) -> None:
    """A permitted action runs and the JSON envelope reports
    `{ok: true, action: <name>, pk: <pk>}`."""
    calls: list = []

    def my_action(request, obj):  # noqa: ARG001
        calls.append(obj.pk)
        return None

    def get_change_actions(request, object_id, form_url):  # noqa: ARG001
        return ["my_action"]

    with admin_attr(User, my_action=my_action, get_change_actions=get_change_actions):
        response = superuser_client.post(
            f"{DETAIL_BASE}{some_user.pk}/action/my_action/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["action"] == "my_action"
        assert body["pk"] == str(some_user.pk)
        assert calls == [some_user.pk]


def test_run_object_action_redirect_surfaces_in_envelope(
    superuser_client: Client, some_user,
) -> None:
    """If the action returns an HttpResponse with a Location, the
    envelope surfaces `redirect` so the SPA can follow it without
    parsing HTML (#603, same shape as the changelist runner)."""

    def my_action(request, obj):  # noqa: ARG001
        return HttpResponseRedirect("/admin/auth/user/2/some-flow/")

    def get_change_actions(request, object_id, form_url):  # noqa: ARG001
        return ["my_action"]

    with admin_attr(User, my_action=my_action, get_change_actions=get_change_actions):
        response = superuser_client.post(
            f"{DETAIL_BASE}{some_user.pk}/action/my_action/",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["redirect"] == "/admin/auth/user/2/some-flow/"

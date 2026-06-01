"""Emitted UI / error-envelope strings are translatable (#73).

The package wraps its hard-coded English envelope strings ("Not found.",
"You do not have permission.", etc.) in ``gettext_lazy`` so a consumer
running a non-English admin (with ``LocaleMiddleware`` activating the
request locale) gets localized envelopes. These tests assert the strings
are lazy proxies (wired for translation) and that responses still
serialize to valid JSON regardless of the active locale.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.utils.functional import Promise
from django.utils.translation import override


def test_envelope_messages_are_lazy_translatable() -> None:
    """The canonical envelope message constants are gettext_lazy proxies."""
    from django_admin_rest_api.api.permissions import _FORBIDDEN_BODY
    from django_admin_rest_api.api.permissions import _SESSION_EXPIRED_BODY
    from django_admin_rest_api.api.views.auth import _INVALID_MESSAGE
    from django_admin_rest_api.api.writes import _CONFLICT_MESSAGE
    from django_admin_rest_api.api.writes import _NOT_FOUND_BODY

    assert isinstance(_FORBIDDEN_BODY["error"]["message"], Promise)
    assert isinstance(_SESSION_EXPIRED_BODY["error"]["message"], Promise)
    assert isinstance(_NOT_FOUND_BODY["error"]["message"], Promise)
    assert isinstance(_CONFLICT_MESSAGE, Promise)
    assert isinstance(_INVALID_MESSAGE, Promise)


@pytest.mark.django_db
def test_forbidden_envelope_serializes_under_active_locale(user_client: Client) -> None:
    """A 403 still returns a valid JSON envelope when a locale is active.

    The message is a lazy proxy; ``JsonResponse`` must resolve it cleanly
    against the active locale (here a non-default one) without raising.
    """
    with override("es"):
        response = user_client.get("/admin-api/api/v1/registry/")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    # The message resolves to a string (the package ships no .po, so it is
    # the English source under any locale — the point is it serializes).
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]


@pytest.mark.django_db
def test_not_found_envelope_serializes(superuser_client: Client) -> None:
    """A 404 envelope with a lazy message serializes to valid JSON."""
    response = superuser_client.get("/admin-api/api/v1/auth/nonexistent/")
    assert response.status_code == 404
    assert (
        superuser_client.get("/admin-api/api/v1/auth/nonexistent/").json()["error"]["code"]
        == "not_found"
    )

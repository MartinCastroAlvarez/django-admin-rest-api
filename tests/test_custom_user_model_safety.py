"""Tests that prove the package is safe under a custom AUTH_USER_MODEL (#38).

The package contains zero direct references to ``django.contrib.auth.models.User``
— it accesses the request user through ``request.user`` (which
Django's ``AuthenticationMiddleware`` sets up for any user model) and
admin-registered models through ``model_admin.model``. This file
codifies the property so a refactor that accidentally hardcodes
``User`` is caught at test time, NOT in a consumer's logs.

Spinning up a second Django test project with ``AUTH_USER_MODEL =
"accounts.User"`` is heavy for the value: the relevant abstractions
are testable in a single process via:

1. **Static check** — grep the package source for the forbidden import.
2. **Permission gate behavior** — verify ``is_admin_user`` returns
   ``False`` for a stub user lacking ``is_staff`` (the custom-user-
   model failure mode the defensive ``getattr(..., False)`` covers).
3. **Password endpoint contract** — verify the endpoint 404s when
   ``change_password_form`` is not declared on the admin (the case
   for a custom user model whose ``UserAdmin`` doesn't subclass
   ``django.contrib.auth.admin.UserAdmin``).
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.test import RequestFactory

from django_admin_rest_api.api.permissions import is_admin_user

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "django_admin_rest_api"


# --------------------------------------------------------------------------- #
# 1. Static abstraction — no hardcoded `auth.User`                            #
# --------------------------------------------------------------------------- #
def test_package_source_has_no_hardcoded_user_model_import() -> None:
    """Grep every .py file in the package for ``from django.contrib.auth.models
    import User``. The package must use ``get_user_model()`` (or stay
    abstract entirely, which it does today) so a consumer with
    ``AUTH_USER_MODEL = "accounts.User"`` isn't broken by a
    hardcoded reference."""
    hits: list[tuple[str, int, str]] = []
    pattern = "from django.contrib.auth.models import User"
    for path in PACKAGE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern in line:
                hits.append((str(path.relative_to(PACKAGE_ROOT)), lineno, line.strip()))
    assert hits == [], (
        "package must not hardcode `auth.User` — use `get_user_model()` "
        f"or stay abstract. found: {hits}"
    )


# --------------------------------------------------------------------------- #
# 2. Defensive `is_staff` lookup — works on a stub user lacking the attr      #
# --------------------------------------------------------------------------- #
def test_is_admin_user_returns_false_for_stub_user_without_is_staff() -> None:
    """A custom user model that doesn't define ``is_staff`` must not
    crash the permission gate. ``is_admin_user`` uses
    ``getattr(user, "is_staff", False)`` for exactly this reason."""
    request = RequestFactory().get("/")
    # Mimic an authenticated user that lacks `is_staff` entirely.
    request.user = SimpleNamespace(is_authenticated=True, is_active=True)
    assert is_admin_user(request, admin_site=admin.site) is False


def test_is_admin_user_returns_false_for_stub_user_without_is_active() -> None:
    """Same property for `is_active`: a custom user model that omits
    it (or sets it to a falsy non-bool) is rejected."""
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
    assert is_admin_user(request, admin_site=admin.site) is False


# --------------------------------------------------------------------------- #
# 3. Password endpoint 404s when `change_password_form` isn't on the admin    #
#    — the failure mode for a custom user model whose admin isn't a UserAdmin #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_password_endpoint_404s_when_admin_has_no_change_password_form(
    superuser_client: Client,
) -> None:
    """Reproduce the custom-user-model scenario where the consumer's
    admin doesn't subclass ``django.contrib.auth.admin.UserAdmin``:
    ``change_password_form`` is absent → 404 (not 500).

    The check is targeted at our stock ``auth.Group`` admin (which
    legitimately has no password-change affordance) — same code path
    a custom user model's admin would hit."""
    from django.contrib.auth.models import Group

    g = Group.objects.create(name="alpha")
    response = superuser_client.post(
        f"/admin-api/api/v1/auth/group/{g.pk}/password/",
        data='{"password1": "x", "password2": "x"}',
        content_type="application/json",
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# 4. Sanity — the default user model is whatever get_user_model says          #
# --------------------------------------------------------------------------- #
def test_get_user_model_is_resolvable() -> None:
    """Boring sanity: under the default test settings, `get_user_model`
    resolves. Sentinel against a future regression that breaks
    `AUTH_USER_MODEL` resolution on a test that didn't bother to set
    it explicitly."""
    User = get_user_model()
    assert User is not None
    assert hasattr(User, "USERNAME_FIELD")

"""Tests for ``python manage.py admin_rest_api_check`` (#37)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_smoke_command_reports_healthy_install_on_test_project() -> None:
    """Against the bundled test_project, the command should exit 0 and
    print every registered ModelAdmin."""
    out = StringIO()
    err = StringIO()
    call_command("admin_rest_api_check", stdout=out, stderr=err)
    output = out.getvalue()

    assert "AdminSite path: django.contrib.admin.site" in output
    assert "resolves to an AdminSite" in output
    assert "django.middleware.csrf.CsrfViewMiddleware" in output
    assert "django.contrib.sessions.middleware.SessionMiddleware" in output
    assert "django.contrib.auth.middleware.AuthenticationMiddleware" in output
    # Test project registers auth.user, auth.group, and uploads.document.
    assert "auth.user" in output
    assert "auth.group" in output
    assert "uploads.document" in output
    assert "OK" in output


def test_smoke_command_exits_nonzero_when_admin_site_is_bogus(settings) -> None:
    """A typo'd ADMIN_SITE dotted path must produce a CommandError so
    CI / deploy preflight catches it before the request path does."""
    settings.DJANGO_ADMIN_REST_API = {"ADMIN_SITE": "no.such.module.site_typo"}

    from django_admin_rest_api import conf as _conf

    _conf._cached = None
    try:
        with pytest.raises(CommandError) as exc_info:
            call_command("admin_rest_api_check", stdout=StringIO())
        assert "no.such.module.site_typo" in str(exc_info.value)
    finally:
        _conf._cached = None


def test_smoke_command_exits_nonzero_when_required_middleware_missing(settings) -> None:
    """A MIDDLEWARE list missing CsrfViewMiddleware should be flagged."""
    settings.MIDDLEWARE = [
        m for m in settings.MIDDLEWARE if m != "django.middleware.csrf.CsrfViewMiddleware"
    ]
    with pytest.raises(CommandError) as exc_info:
        call_command("admin_rest_api_check", stdout=StringIO())
    assert "CsrfViewMiddleware" in str(exc_info.value)

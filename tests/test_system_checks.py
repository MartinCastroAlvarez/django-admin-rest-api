"""Tests for the package's Django system checks (#31, #32, #33).

Each check is invoked directly (`check_*(None)`) rather than going
through ``django.core.management.call_command("check")`` so the test
can assert the exact ``id`` / ``level`` / ``msg`` shape without
fighting Django's check-registry discovery order.
"""

from __future__ import annotations

import pytest
from django.core.checks import Error
from django.core.checks import Warning as ChecksWarning

from django_admin_rest_api.system_checks import check_admin_site_resolves
from django_admin_rest_api.system_checks import check_required_middleware
from django_admin_rest_api.system_checks import check_settings_dict_name


# --------------------------------------------------------------------------- #
# W001 — settings dict-name typo                                              #
# --------------------------------------------------------------------------- #
def test_w001_no_warning_when_only_canonical_dict_is_set(settings) -> None:
    settings.DJANGO_ADMIN_REST_API = {"MAX_PAGE_SIZE": 100}
    issues = check_settings_dict_name(None)
    assert issues == []


def test_w001_warns_on_typo_attribute(settings) -> None:
    settings.DJANGO_ADMIN_REST_API_CONFIG = {"foo": "bar"}
    issues = check_settings_dict_name(None)
    assert len(issues) == 1
    issue = issues[0]
    assert isinstance(issue, ChecksWarning)
    assert issue.id == "django_admin_rest_api.W001"
    assert "DJANGO_ADMIN_REST_API_CONFIG" in issue.msg


def test_w001_warns_on_multiple_typo_attributes(settings) -> None:
    settings.DJANGO_ADMIN_REST_API_CONFIG = {}
    settings.DJANGO_ADMIN_REST_API_SETTINGS = {}
    issues = check_settings_dict_name(None)
    # The check emits one Warning that lists every suspect, not one
    # Warning per suspect — keeps the system-check output uncluttered.
    assert len(issues) == 1
    msg = issues[0].msg
    assert "DJANGO_ADMIN_REST_API_CONFIG" in msg
    assert "DJANGO_ADMIN_REST_API_SETTINGS" in msg


# --------------------------------------------------------------------------- #
# E001 — ADMIN_SITE resolves                                                  #
# --------------------------------------------------------------------------- #
def test_e001_no_error_on_default_admin_site() -> None:
    issues = check_admin_site_resolves(None)
    assert issues == []


def test_e001_errors_on_unresolvable_dotted_path(settings) -> None:
    settings.DJANGO_ADMIN_REST_API = {"ADMIN_SITE": "no.such.module.site_typo"}
    from django_admin_rest_api import conf as _conf

    _conf._cached = None
    try:
        issues = check_admin_site_resolves(None)
        assert len(issues) == 1
        issue = issues[0]
        assert isinstance(issue, Error)
        assert issue.id == "django_admin_rest_api.E001"
        assert "no.such.module.site_typo" in issue.msg
    finally:
        _conf._cached = None


def test_e001_errors_on_non_adminsite_object(settings) -> None:
    """A dotted path that resolves to something OTHER than an `AdminSite`
    must also error. Point at the `AdminSite` CLASS (which exists and
    imports cleanly but isn't an instance) to hit the isinstance branch."""
    settings.DJANGO_ADMIN_REST_API = {"ADMIN_SITE": "django.contrib.admin.sites.AdminSite"}
    from django_admin_rest_api import conf as _conf

    _conf._cached = None
    try:
        issues = check_admin_site_resolves(None)
        assert len(issues) == 1
        assert "not an `AdminSite` instance" in issues[0].msg
    finally:
        _conf._cached = None


# --------------------------------------------------------------------------- #
# W002 — required middleware                                                  #
# --------------------------------------------------------------------------- #
def test_w002_no_warning_when_all_required_middleware_present() -> None:
    issues = check_required_middleware(None)
    assert issues == []


def test_w002_warns_for_each_missing_middleware(settings) -> None:
    settings.MIDDLEWARE = [
        # Drop CSRF and Session, keep Authentication.
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
    issues = check_required_middleware(None)
    assert len(issues) == 2
    ids = {i.id for i in issues}
    assert ids == {"django_admin_rest_api.W002"}
    msgs = " ".join(i.msg for i in issues)
    assert "CsrfViewMiddleware" in msgs
    assert "SessionMiddleware" in msgs


def test_w002_warns_when_all_required_middleware_missing(settings) -> None:
    settings.MIDDLEWARE = []
    issues = check_required_middleware(None)
    assert len(issues) == 3


@pytest.mark.django_db
def test_django_check_command_picks_up_our_checks_when_typo_is_present(settings) -> None:
    """Smoke: `django.core.management.call_command("check")` surfaces
    W001 when a typo attribute is set."""
    from io import StringIO

    from django.core.management import call_command

    settings.DJANGO_ADMIN_REST_API_TYPO = {}
    out = StringIO()
    err = StringIO()
    # `call_command("check")` raises SystemCheckError on Errors and
    # prints Warnings — we have a Warning so the call succeeds.
    call_command("check", stdout=out, stderr=err)
    combined = out.getvalue() + err.getvalue()
    assert "django_admin_rest_api.W001" in combined

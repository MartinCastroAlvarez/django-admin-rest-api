"""Structured security logging at denial / failed-login boundaries (#67).

The package emits one ``django_admin_rest_api.security`` record per 403
permission denial (``permissions.forbidden_response``) and per failed login
(``views/auth``), carrying ``{user, path, method, decision}`` — never the
password, never request-body PII. These tests assert the record fires with
the right structured fields and that no secret leaks into it.
"""

from __future__ import annotations

import logging

import pytest
from django.test import Client

_LOGGER = "django_admin_rest_api.security"


@pytest.mark.django_db
def test_forbidden_logs_security_event(
    caplog: pytest.LogCaptureFixture, user_client: Client
) -> None:
    """A 403 from the staff gate emits a forbidden security record."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        response = user_client.get("/admin-api/api/v1/registry/")
    assert response.status_code == 403
    records = [r for r in caplog.records if r.name == _LOGGER]
    assert records, "expected a security log record on 403"
    record = records[-1]
    assert record.decision == "forbidden"
    assert record.method == "GET"
    assert record.path == "/admin-api/api/v1/registry/"
    # The user is authenticated (non-staff) so the pk is recorded, not anon.
    assert record.user != "anon"


@pytest.mark.django_db
def test_failed_login_logs_security_event_without_password(
    caplog: pytest.LogCaptureFixture, db: object
) -> None:
    """A failed login emits a login_failed record that never contains the password."""
    client = Client()
    secret = "sup3r-s3cret-pw"  # noqa: S105 — test literal, asserted absent from logs
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        response = client.post(
            "/admin-api/api/v1/login/",
            data={"username": "ghost", "password": secret},
            content_type="application/json",
        )
    assert response.status_code == 403
    records = [r for r in caplog.records if r.name == _LOGGER]
    assert records, "expected a security log record on failed login"
    record = records[-1]
    assert record.decision == "login_failed"
    assert record.method == "POST"
    assert record.user == "anon"
    # The password must never appear in the rendered message or any field.
    assert secret not in record.getMessage()
    assert secret not in str(record.__dict__)


@pytest.mark.django_db
def test_successful_request_logs_nothing(
    caplog: pytest.LogCaptureFixture, superuser_client: Client
) -> None:
    """An allowed request emits no security record (only denials are logged)."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        response = superuser_client.get("/admin-api/api/v1/registry/")
    assert response.status_code == 200
    assert [r for r in caplog.records if r.name == _LOGGER] == []

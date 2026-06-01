"""405 responses use the canonical JSON envelope (#65).

Every API view subclasses ``BaseAPIView``, which overrides
``http_method_not_allowed`` so a disallowed HTTP method returns the same
``{"error": {"code": "method_not_allowed", ...}}`` envelope the OpenAPI
schema advertises — not Django's bare ``HttpResponseNotAllowed`` HTML body.
The ``Allow`` header (the permitted methods) must still be present so the
response stays a spec-compliant 405.
"""

from __future__ import annotations

import pytest
from django.test import Client


@pytest.mark.django_db
def test_instance_view_put_returns_json_envelope(superuser_client: Client) -> None:
    """PUT on the instance route (GET/PATCH/DELETE only) → JSON 405 envelope."""
    response = superuser_client.put("/admin-api/api/v1/auth/user/1/")
    assert response.status_code == 405
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["error"]["code"] == "method_not_allowed"
    assert body["error"]["message"]
    # The Allow header is preserved and lists the methods that ARE allowed.
    assert "Allow" in response
    allow = response["Allow"]
    assert "GET" in allow and "PATCH" in allow and "DELETE" in allow
    assert "PUT" not in allow


@pytest.mark.django_db
def test_bulk_get_returns_json_envelope(superuser_client: Client) -> None:
    """GET on the PATCH-only bulk route → JSON 405 envelope with Allow: PATCH."""
    response = superuser_client.get("/admin-api/api/v1/auth/user/bulk/")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "PATCH" in response["Allow"]


@pytest.mark.django_db
def test_registry_post_returns_json_envelope(superuser_client: Client) -> None:
    """POST on the GET-only registry route → JSON 405 envelope."""
    response = superuser_client.post("/admin-api/api/v1/registry/")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response["Allow"]


@pytest.mark.django_db
def test_405_is_not_cacheable(superuser_client: Client) -> None:
    """A method-routing decision carries ``Cache-Control: no-store``."""
    response = superuser_client.put("/admin-api/api/v1/auth/user/1/")
    assert response["Cache-Control"] == "no-store"

"""Field derivation must match what the HTML admin actually renders.

Django's change view builds its form from ``flatten_fieldsets(
get_fieldsets(request, obj))`` — NOT from ``get_fields`` — so a
``ModelAdmin`` whose ``fields`` attribute diverges from its fieldsets
(the real-world case: a project decorator that swaps display-method
names like ``get_amount`` into ``fields`` while ``get_fieldsets``
keeps returning the raw editable names) still edits the raw field in
the HTML admin. Deriving this API's visible/writable sets from
``get_fields`` silently rejected those same writes with
``Unknown field`` — the two surfaces disagreed on the same admin.

Observed live: an operations engineer could fix a mis-parsed bank
statement balance through the HTML admin while the identical PATCH
through this API was rejected.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import Client

from django_admin_rest_api.api.writes import writable_field_names

DETAIL_URL = "/admin-api/api/v1/auth/group/{pk}/"
FORM_SPEC_URL = "/admin-api/api/v1/auth/group/{pk}/form-spec/"


@contextmanager
def _admin_attrs(model_cls: type, **attrs: Any) -> Iterator[None]:
    """Temporarily set attributes (callables bound, values verbatim) on the
    registered admin — ``tests.helpers.admin_override`` binds callables only,
    and this scenario needs the ``fields`` tuple overridden too."""
    model_admin = admin.site._registry[model_cls]
    sentinel = object()
    originals = {name: getattr(model_admin, name, sentinel) for name in attrs}
    try:
        for name, value in attrs.items():
            if callable(value):
                setattr(model_admin, name, value.__get__(model_admin))
            else:
                setattr(model_admin, name, value)
        yield
    finally:
        for name, original in originals.items():
            if original is sentinel:
                delattr(model_admin, name)
            else:
                setattr(model_admin, name, original)


def _divergent_admin() -> dict[str, Any]:
    """``fields`` lists only a display method; ``get_fieldsets`` renders the
    raw editable field — exactly the HTML-vs-API divergence under test."""
    return dict(
        fields=("get_name",),
        get_name=lambda self, obj: f"pretty {obj.name}",
        get_fieldsets=lambda self, request, obj=None: [
            ("Information", {"fields": ("name", "get_name")}),
        ],
        get_readonly_fields=lambda self, request, obj=None: ("get_name",),
    )


@pytest.mark.django_db
def test_writable_set_follows_fieldsets_not_fields_attr(rf, admin_user) -> None:
    group = Group.objects.create(name="orig")
    request = rf.get("/")
    request.user = admin_user
    with _admin_attrs(Group, **_divergent_admin()):
        model_admin = admin.site._registry[Group]
        writable = writable_field_names(Group, model_admin, request, group)
    assert "name" in writable  # rendered editable by the HTML admin
    assert "get_name" not in writable  # readonly display method


@pytest.mark.django_db
def test_patch_accepts_field_rendered_by_fieldsets(superuser_client: Client) -> None:
    group = Group.objects.create(name="orig")
    with _admin_attrs(Group, **_divergent_admin()):
        response = superuser_client.patch(
            DETAIL_URL.format(pk=group.pk),
            data=json.dumps({"name": "corrected"}),
            content_type="application/json",
        )
    assert response.status_code == 200, response.content
    group.refresh_from_db()
    assert group.name == "corrected"


@pytest.mark.django_db
def test_form_spec_surfaces_fieldsets_fields_as_editable(superuser_client: Client) -> None:
    group = Group.objects.create(name="orig")
    with _admin_attrs(Group, **_divergent_admin()):
        response = superuser_client.get(FORM_SPEC_URL.format(pk=group.pk))
    assert response.status_code == 200, response.content
    fields = response.json()["fields"]
    assert "name" in fields and fields["name"]["readonly"] is False
    assert "get_name" in fields and fields["get_name"]["readonly"] is True


@pytest.mark.django_db
def test_plain_admin_unchanged(rf, admin_user) -> None:
    """No fieldsets override → stock ``get_fieldsets`` falls back to
    ``get_fields`` — plain admins keep their exact previous behavior."""
    group = Group.objects.create(name="orig")
    request = rf.get("/")
    request.user = admin_user
    model_admin = admin.site._registry[Group]
    writable = writable_field_names(Group, model_admin, request, group)
    assert "name" in writable

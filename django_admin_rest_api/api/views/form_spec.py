"""``GET /api/v1/<app>/<model>/[<pk>|add]/form-spec/`` — the ModelAdmin form spec.

One endpoint, two routes:

- ``<app>/<model>/add/form-spec/``      → the add-view form (``obj=None``).
- ``<app>/<model>/<pk>/form-spec/``     → the change-view form for ``<pk>``.

Both return the payload built by :func:`build_form_spec` (the shared
resolver the MCP ``admin.form_spec`` tool also calls), so the SPA renders
the same ModelAdmin-resolved form the legacy ``/admin/`` change page would
(Issue #59).

Hard rules (`SECURITY.md` §3):

- Rule 1:  staff + ``AdminSite.has_permission`` gate.
- Rule 3:  model resolved through ``admin.site._registry``.
- Rule 5:  per-object ``has_view_permission`` gate on the change route;
           ``has_add_permission`` on the add route (create is gated on
           add, not view — same as the add-form schema endpoint).
- Rule 6:  fields come from ``ModelAdmin.get_form`` /
           ``get_fieldsets`` / ``get_readonly_fields``; sensitive-name
           denylist applied on top by the shared resolver.
- Rule 10: object loaded via ``ModelAdmin.get_object`` — never
           ``Model.objects.all()``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse

from django_admin_rest_api.api.form_spec import build_form_spec
from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.views.base import BaseAPIView
from django_admin_rest_api.api.writes import load_object_or_none
from django_admin_rest_api.api.writes import not_found_response


class FormSpecView(BaseAPIView):
    """``GET <app>/<model>/[<pk>|add]/form-spec/`` — ModelAdmin form spec."""

    http_method_names = ["get"]

    def get(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        """Return the resolved form spec for the add (``pk is None``) or
        change (``pk`` given) view.

        Gates, in order:

        1. ``is_admin_user`` — 403 if not authenticated active staff.
        2. ``resolve_model`` — 404 if model unknown or unviewable.
        3. add route: ``has_add_permission`` — 403 if the user can't add.
           change route: ``load_object_or_none`` (404) then per-object
           ``has_view_permission`` (403).
        """
        admin_site = get_admin_site()
        if not is_admin_user(request, admin_site=admin_site):
            return forbidden_response(request)

        resolved = resolve_model(admin_site, request, app_label, model_name)
        if resolved is None:
            return not_found_response()
        model, model_admin = resolved

        if pk is None:
            # Add-view form — gated on add (not view): a user who can view
            # but not add must not be handed an add form.
            if not model_admin.has_add_permission(request):
                return forbidden_response(request)
            obj = None
        else:
            obj = load_object_or_none(model, model_admin, request, pk)
            if obj is None:
                return not_found_response()
            # Per-object view gate (rule 5) — same as the detail endpoint.
            # Save-button gating (change/add) is surfaced by the detail /
            # registry permission payloads the SPA already reads.
            if not model_admin.has_view_permission(request, obj):
                return forbidden_response(request)

        payload = build_form_spec(model, model_admin, request, obj, admin_site=admin_site)
        response = JsonResponse(payload, status=200)
        # Per-user, permission-gated payload — never cached (contract §1.2).
        response["Cache-Control"] = "no-store"
        return response

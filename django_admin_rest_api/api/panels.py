"""Per-model panel endpoint (Issue #65).

Consumers register custom data endpoints under a model's detail URL
without re-implementing auth / model-resolution / permission gates.

The opt-in is **plain Django**: declare a ``panels`` dict directly on
any ``ModelAdmin``. No package-specific mixin is required.

::

    class InvoiceAdmin(admin.ModelAdmin):
        panels = {"audit_trail": "get_audit_trail"}

        def get_audit_trail(self, request, obj):
            return {"entries": [...]}

    # Then register the admin the usual way (decorator or call form).

URL shape: ``GET …/<app>/<model>/<pk>/panel/<name>/``

Hard rules (`SECURITY.md` §3):

- Rule 5: ``has_view_permission(request, obj)`` per-object gate.
- Rule 10: Object loaded via ``ModelAdmin.get_queryset(request)``.
- Rule 12: Unknown panel names → 404 (no oracle).
- Panel name is re-resolved through ``model_admin.panels`` — never
  used as a direct method lookup against the admin instance.
"""

from __future__ import annotations

import warnings
from typing import Any
from typing import ClassVar

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse

from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.views.base import BaseAPIView
from django_admin_rest_api.api.writes import load_object_or_none
from django_admin_rest_api.api.writes import not_found_response


class PanelEndpointsMixin:
    """Deprecated — kept as a no-op shim for backward compatibility (#34).

    The runtime resolves panels via plain ``getattr(model_admin,
    "panels", {})`` regardless of whether the consumer mixes this
    class in. Subclassing it is no longer required; declaring
    ``panels = {...}`` directly on any ``ModelAdmin`` is enough.

    A consumer who still mixes it in gets a single
    ``DeprecationWarning`` at class-definition time. The shim will be
    removed in a future major release; for now it stays so existing
    code keeps working.
    """

    panels: ClassVar[dict[str, str]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "PanelEndpointsMixin is deprecated and no longer required. "
            "Declare `panels = {...}` directly on your ModelAdmin — the "
            "shim will be removed in a future major release.",
            DeprecationWarning,
            stacklevel=2,
        )


class PanelView(BaseAPIView):
    """``GET /api/v1/<app>/<model>/<pk>/panel/<panel_name>/``."""

    http_method_names = ["get"]

    def get(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: str,
        panel_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        """Render one opt-in ``ModelAdmin.panels`` entry for an instance.

        Admins that don't declare ``panels`` return 404 on every panel
        URL; an unknown ``panel_name`` is likewise a 404.
        """
        admin_site = get_admin_site()
        if not is_admin_user(request, admin_site=admin_site):
            return forbidden_response(request)

        resolved = resolve_model(admin_site, request, app_label, model_name)
        if resolved is None:
            return not_found_response()
        model, model_admin = resolved

        # Opt-in: admins that don't declare ``panels`` return 404 on
        # every panel URL.
        panels = getattr(model_admin, "panels", None) or {}
        if not isinstance(panels, dict) or panel_name not in panels:
            return not_found_response()

        method_name = panels[panel_name]
        # The handler name comes from the admin's own dict, not from
        # the URL — never let the URL pick a method directly.
        if not isinstance(method_name, str):
            return not_found_response()
        handler = getattr(model_admin, method_name, None)
        if not callable(handler):
            return not_found_response()

        obj = load_object_or_none(model, model_admin, request, pk)
        if obj is None:
            return not_found_response()
        if not model_admin.has_view_permission(request, obj):
            return forbidden_response(request)

        # The panel handler owns its own response shape. We surface
        # whatever JSON-serialisable value it returns. If it raises,
        # let it propagate to 500 — the consumer's handler is their
        # own surface and we don't want to mask their bug.
        result = handler(request, obj)
        response = JsonResponse({"panel": panel_name, "data": result}, status=200)
        response["Cache-Control"] = "no-store"
        return response

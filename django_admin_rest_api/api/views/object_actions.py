"""``POST /api/v1/<app>/<model>/<pk>/action/<name>/`` — run one per-object action.

Wire contract: ``docs/api-contract.md`` §5.5 (#603).

Powers the `django-object-actions` / `change_actions = [...]`
extension point: per-object actions visible on the detail page, one
click per row, no multi-select. The companion descriptor list is
exposed by the detail endpoint as ``data.object_actions`` so the SPA
can render one button per entry; this view is the runner those
buttons POST to.

Hard rules (`SECURITY.md` §3, `ACCEPTANCE.md` §3.1):

- Rule 1:  Staff + ``AdminSite.has_permission`` gate.
- Rule 3:  Model resolved through ``admin.site._registry`` (B-7).
- Rule 5:  ``has_change_permission(request, obj)`` per-object gate —
           per-object actions are change-shaped (parity with the
           legacy admin's change-page surface).
- Rule 10: Object resolved through ``ModelAdmin.get_queryset(request)``
           — never ``Model.objects.all()`` (B-2).
- Rule 12: An unknown action name returns 404; an action callable
           may raise, in which case we let it propagate as 500 so
           the consumer sees the real cause in their logs (same
           posture as the changelist actions view).
- CSRF:    No ``@csrf_exempt`` — Django's middleware enforces.

Action discovery is duck-typed on ``ModelAdmin.get_change_actions``
(rather than imported from ``django-object-actions``) so the package
stays free of a runtime dep on a specific third-party extension and
admins that expose the same shape via any other mechanism work without
configuration.
"""

from __future__ import annotations

from typing import Any

from django.contrib.messages import get_messages
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.generic import View

from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.writes import not_found_response


class ObjectActionView(View):
    """``POST /<app>/<model>/<pk>/action/<name>/``."""

    http_method_names = ["post"]

    def post(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: str,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        # noqa: ARG002 — args/kwargs present to satisfy the CBV signature.
        admin_site = get_admin_site()
        if not is_admin_user(request, admin_site=admin_site):
            return forbidden_response(request)

        resolved = resolve_model(admin_site, request, app_label, model_name)
        if resolved is None:
            return not_found_response()
        _model, model_admin = resolved

        # Object resolved through the admin's own get_queryset (Rule 10
        # / B-2) so an action can never reach a row the user couldn't
        # already see.
        try:
            obj = model_admin.get_queryset(request).get(pk=pk)
        except model_admin.model.DoesNotExist:
            return not_found_response()

        # Change-permission gate per-object — matches the legacy admin's
        # posture for the change-page action surface.
        if not model_admin.has_change_permission(request, obj):
            return forbidden_response(request)

        # Re-resolve the action name through the admin's own
        # `get_change_actions` (never trust the URL until the admin
        # confirms the action exists for THIS user + THIS object).
        get_change_actions = getattr(model_admin, "get_change_actions", None)
        if not callable(get_change_actions):
            return not_found_response()
        try:
            permitted = list(get_change_actions(request, str(obj.pk), "") or [])
        except Exception:
            permitted = []
        if name not in permitted:
            return not_found_response()

        action_callable = getattr(model_admin, name, None)
        if action_callable is None or not callable(action_callable):
            return not_found_response()

        # Run inside a transaction so a partial mutation rolls back
        # cleanly when the action raises (mirrors the changelist
        # actions view's posture).
        with transaction.atomic():
            result = action_callable(request, obj)

        # Drain any messages the action queued via `message_user` (#442)
        # so the client can toast them — iterating consumes them, so
        # they don't also leak into the session for the next page render.
        messages = [
            {"level": m.level_tag or "info", "message": str(m)} for m in get_messages(request)
        ]

        # Django admin's action contract: the callable may return an
        # `HttpResponse` (typically a redirect to a confirmation page
        # or an intermediate form). Surface it as a JSON envelope the
        # SPA can follow without parsing HTML.
        if isinstance(result, HttpResponse):
            body: dict[str, Any] = {"redirect": result["Location"]} if "Location" in result else {}
            body.update({"ok": True, "action": name, "messages": messages})
            response = JsonResponse(body, status=200)
        else:
            response = JsonResponse(
                {"ok": True, "action": name, "pk": str(obj.pk), "messages": messages},
                status=200,
            )
        response["Cache-Control"] = "no-store"
        return response

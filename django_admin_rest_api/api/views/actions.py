"""``POST /api/v1/<app>/<model>/actions/<action_name>/`` — run an admin action.

Wire contract: ``docs/api-contract.md`` §5.4.

Powers Django admin's ``actions = [...]`` mechanism for the client. The
caller picks an action by name and a list of pks; the package
re-resolves the action through ``ModelAdmin.get_actions(request)``
(never trusts the action name client-side), then runs it over the
queryset narrowed to those pks **and** the admin's own
``get_queryset(request)`` (so the action cannot touch rows the user
isn't allowed to see).

Hard rules (`SECURITY.md` §3):

- Rule 1:  Staff + ``AdminSite.has_permission`` gate.
- Rule 3:  Model resolved through ``admin.site._registry`` (B-7).
- Rule 5:  ``has_change_permission`` per-action gate (matches the
           legacy admin's posture — actions are change-shaped).
- Rule 10: Queryset starts at ``ModelAdmin.get_queryset(request)``
           and is narrowed by ``pk__in=<pks>`` — never bypasses the
           admin's row-level filtering (B-2).
- Rule 12: Bad input (unknown action name, empty pks) returns 400/404
           with the canonical envelope. Action callables may raise;
           we let those propagate as 500 so the consumer sees the
           real cause in their logs (we don't want to silently swallow
           an admin author's bug).
- CSRF:    No ``@csrf_exempt`` — Django's middleware enforces.
"""

from __future__ import annotations

from typing import Any

from django.contrib.messages import get_messages
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.generic import View

from django_admin_rest_api.api.actions_meta import _classify_action
from django_admin_rest_api.api.actions_meta import actions_payload
from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.writes import bad_request
from django_admin_rest_api.api.writes import not_found_response
from django_admin_rest_api.api.writes import parse_json_body

# ``_classify_action`` and ``actions_payload`` now live in the lower-layer
# ``actions_meta`` module (#55) so ``registry`` can import them without a
# cycle. Re-exported here for backwards compatibility with importers that
# reference them via ``views.actions``.
__all__ = ["ActionView", "_classify_action", "actions_payload"]


class ActionView(View):
    """``POST /api/v1/<app>/<model>/actions/<action_name>/``.

    Body: ``{"pks": [<pk>, ...], "confirmed": <bool>}``. ``confirmed``
    is informational only in v1 — the client passes it to indicate the
    user has acknowledged a confirmation step; the backend doesn't
    short-circuit on it (the action callable owns confirmation
    semantics).
    """

    http_method_names = ["post"]

    def post(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        action_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        """Run a named admin action against the selected rows (contract §5.4)."""
        admin_site = get_admin_site()
        if not is_admin_user(request, admin_site=admin_site):
            return forbidden_response(request)

        resolved = resolve_model(admin_site, request, app_label, model_name)
        if resolved is None:
            return not_found_response()
        _model, model_admin = resolved

        # Re-resolve the action through the admin — never trust the
        # action_name from the URL until ModelAdmin.get_actions
        # confirms it exists for this user.
        actions = model_admin.get_actions(request) or {}
        # ``actions[name]`` is a ``(callable, name, description)`` tuple
        # per Django admin's convention. ``.get`` + a None check both
        # rejects an unknown action and narrows the optional lookup.
        action_entry = actions.get(action_name)
        if action_entry is None:
            return not_found_response()
        action_callable = action_entry[0]

        # Actions are change-shaped — the legacy admin gates them
        # behind change permission. Match that posture so a user
        # who cannot edit a row cannot run an action on it either.
        if not model_admin.has_change_permission(request):
            return forbidden_response(request)

        parsed = parse_json_body(request)
        if isinstance(parsed, HttpResponse):
            return parsed
        payload: dict[str, Any] = parsed

        pks = payload.get("pks", [])
        if not isinstance(pks, list) or not pks:
            return bad_request("`pks` must be a non-empty list.")

        # DoS guard (#41): cap the selection size so a crafted POST
        # cannot ask an expensive action to fan out across an
        # unbounded queryset. Mirrors ``MAX_PAGE_SIZE``'s posture on
        # the list endpoint. ``0`` (or below) disables the cap so
        # operators with legitimate large-selection workflows can opt
        # out via settings.
        from django_admin_rest_api import conf

        cap = int(conf.MAX_ACTION_PKS or 0)
        if cap > 0 and len(pks) > cap:
            return bad_request(
                f"`pks` length ({len(pks)}) exceeds the configured cap of {cap}; "
                "increase ``DJANGO_ADMIN_REST_API['MAX_ACTION_PKS']`` if you have a "
                "legitimate workflow."
            )

        # The client runs its own styled confirm dialog, which stands in
        # for Django's intermediate HTML confirmation page. When it
        # reports the user confirmed, signal that to two-phase actions
        # that gate on the admin's ``post`` flag — most importantly the
        # built-in ``delete_selected``, which only deletes (via
        # ``ModelAdmin.delete_queryset``) when ``request.POST['post']``
        # is set and otherwise just renders the confirmation page.
        # Without this the client confirm would no-op: the page would be
        # rendered server-side and nothing deleted.
        if payload.get("confirmed"):
            # ``.copy()`` yields a *mutable* QueryDict; set the flag on it,
            # then swap it in. The attribute is typed immutable in the
            # stubs, so the assignment needs an explicit ignore.
            mutable_post = request.POST.copy()
            mutable_post["post"] = "yes"
            request.POST = mutable_post  # type: ignore[assignment]

        # Narrow the queryset by both the admin's own get_queryset
        # (Rule 10) AND the pk filter. Order matters: get_queryset
        # FIRST, so the pk filter only sees rows the user could
        # already see — an action cannot reach rows behind
        # ``get_queryset``'s gate.
        queryset = model_admin.get_queryset(request).filter(pk__in=pks)

        # Signature inspection drives the call shape. ``batch`` keeps
        # Django's stock ``(modeladmin, request, queryset)`` contract;
        # ``detail`` passes ``str(pk)`` so a consumer can write actions
        # as ``(self, request, obj_id: str)`` and have them surface on
        # the SPA's detail page without any new endpoint.
        target = _classify_action(action_callable)
        if target == "detail":
            if len(pks) != 1:
                return bad_request(
                    "This action targets a single object; pass exactly one entry in `pks`."
                )
            # Pre-resolve to confirm the row is in the user-visible
            # queryset (the same perm gate batch actions inherit
            # implicitly via the narrowed queryset).
            if not queryset.exists():
                return not_found_response()
            single_pk = str(pks[0])
            with transaction.atomic():
                result = action_callable(model_admin, request, single_pk)
        else:
            with transaction.atomic():
                result = action_callable(model_admin, request, queryset)

        # Surface any messages the action queued via
        # ``ModelAdmin.message_user`` (#442) so the client can toast them —
        # iterating ``get_messages`` consumes them, so they don't also leak
        # into the session for the next page render. ``level_tag`` is
        # Django's "success" / "info" / "warning" / "error" / "debug".
        messages = [
            {"level": m.level_tag or "info", "message": str(m)} for m in get_messages(request)
        ]

        # Django admin's action contract: the callable may return an
        # ``HttpResponse`` (typically a redirect to a confirmation
        # page) — we surface that as a JSON envelope so the client can
        # follow it without parsing HTML.
        if isinstance(result, HttpResponse):
            body: dict[str, Any] = {"redirect": result["Location"]} if "Location" in result else {}
            body.update({"executed": True, "action": action_name, "messages": messages})
            response = JsonResponse(body, status=200)
        else:
            response = JsonResponse(
                {"executed": True, "action": action_name, "pks": list(pks), "messages": messages},
                status=200,
            )
        response["Cache-Control"] = "no-store"
        return response

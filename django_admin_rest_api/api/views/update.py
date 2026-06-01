"""``PATCH /api/v1/<app>/<model>/<pk>/`` — partial update endpoint.

Wire contract: ``docs/api-contract.md`` §5.2.

Hard rules (`SECURITY.md` §3):

- Rule 3:  Model resolved through ``admin.site._registry`` (B-7).
- Rule 5:  ``has_change_permission(request, obj)`` per-object gate.
- Rule 6:  Writes go through ``ModelAdmin.get_form()`` then
           ``save_model(..., change=True)`` (B-3).
- Rule 10: Queryset starts at ``ModelAdmin.get_queryset(request)`` —
           never ``Model.objects.all()`` (B-2).
- Rule 12: Writes to ``readonly`` / ``exclude`` keys → 400 (S-31, B-3).
- CSRF:    No ``@csrf_exempt`` — Django's middleware enforces.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import TooManyFieldsSent
from django.db.models import FileField
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParser
from django.http.multipartparser import MultiPartParserError
from django.views.generic import View

from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.views.detail import _build_payload
from django_admin_rest_api.api.writes import bad_request
from django_admin_rest_api.api.writes import form_errors_to_envelope
from django_admin_rest_api.api.writes import load_object_or_none
from django_admin_rest_api.api.writes import merged_initial_for_update
from django_admin_rest_api.api.writes import not_found_response
from django_admin_rest_api.api.writes import parse_json_body
from django_admin_rest_api.api.writes import readonly_or_excluded_names
from django_admin_rest_api.api.writes import reject_forbidden_keys
from django_admin_rest_api.api.writes import save_through_admin
from django_admin_rest_api.api.writes import validation_failed
from django_admin_rest_api.api.writes import writable_field_names


class UpdateView(View):
    """``PATCH /api/v1/<app_label>/<model_name>/<pk>/``."""

    http_method_names = ["patch"]

    def patch(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        """Partially update an instance (contract §5.2).

        PATCH semantics: any field the payload omits keeps its
        current value. The implementation builds form ``initial``
        data by overlaying the payload on the instance's current
        values, then runs ``ModelAdmin.get_form()`` exactly like the
        Django admin change view.

        Gates: ``is_admin_user`` → ``resolve_model`` →
        ``load_object_or_none`` (uses the admin's queryset, never
        ``Model.objects.all()``) → ``has_change_permission(request,
        obj)`` per-object gate (rule 5).

        Same payload-shape validation as create (unknown / readonly /
        excluded / sensitive keys → 400). Write path goes through
        ``form.save(commit=False)`` →
        ``model_admin.save_model(..., change=True)`` (rule 6 / B-3),
        wrapped in ``transaction.atomic()``.
        """
        admin_site = get_admin_site()
        if not is_admin_user(request, admin_site=admin_site):
            return forbidden_response(request)

        resolved = resolve_model(admin_site, request, app_label, model_name)
        if resolved is None:
            return not_found_response()
        model, model_admin = resolved

        obj = load_object_or_none(model, model_admin, request, pk)
        if obj is None:
            return not_found_response()

        if not model_admin.has_change_permission(request, obj):
            return forbidden_response(request)

        writable = writable_field_names(model, model_admin, request, obj)
        forbidden = readonly_or_excluded_names(model_admin, request, obj)

        # File uploads (#241) arrive as multipart/form-data. Branch on the
        # content type: multipart feeds the ModelForm request.POST +
        # request.FILES bound to the instance; JSON keeps the PATCH-merge.
        # CSRF is enforced either way (no @csrf_exempt).
        is_multipart = (request.content_type or "").startswith("multipart/form-data")
        if is_multipart:
            # The client submits the full form as multipart, so no PATCH-merge:
            # a file field with no new upload and no clear flag keeps its
            # existing file via ClearableFileInput bound to ``instance``.
            inlines_payload = None
            # Django only auto-populates request.POST / request.FILES for
            # POST requests — a PATCH multipart body is left unparsed, so we
            # parse it ourselves. The body hasn't been read yet on this path.
            form_data: Any
            files: Any
            try:
                # ``request`` is the input stream — exactly what Django's own
                # ``_load_post_and_files`` passes to MultiPartParser; the stub
                # types arg 2 as ``IO[bytes]`` (too narrow), hence the ignore.
                form_data, files = MultiPartParser(
                    request.META,
                    request,  # type: ignore[arg-type]
                    request.upload_handlers,
                    request.encoding,
                ).parse()
            except MultiPartParserError:
                return bad_request("Malformed multipart/form-data body.")
            except (RequestDataTooBig, TooManyFieldsSent):
                # Over-limit upload → canonical JSON envelope, not Django's
                # default 400 page (#448).
                return bad_request("Upload exceeds the configured size or field limits.")
            # ``<field>-clear`` is Django's ClearableFileInput convention for
            # removing an existing file. Allow it through the forbidden-key
            # gate for writable file fields (it isn't a model field name), so
            # an explicit clear isn't rejected as an unknown field. Clearing a
            # field the user can't write still fails — its base name isn't in
            # ``writable``, so the form ignores the stray ``-clear``.
            clear_keys = {
                f"{f.name}-clear"
                for f in model._meta.get_fields()
                if isinstance(f, FileField) and f.name in writable
            }
            submitted_keys: dict[str, Any] = dict.fromkeys(
                k for k in (*form_data, *files) if k not in clear_keys
            )
        else:
            parsed = parse_json_body(request)
            if isinstance(parsed, HttpResponse):
                return parsed
            payload: dict[str, Any] = parsed
            # The optional ``inlines`` block is handled by the formset write
            # path after the parent form saves; strip it from the parent
            # payload so it isn't treated as an unknown parent field key.
            inlines_payload = payload.pop("inlines", None)
            submitted_keys = payload
            form_data = merged_initial_for_update(obj, writable, payload, model)
            files = None

        rejection = reject_forbidden_keys(submitted_keys, writable, forbidden)
        if rejection is not None:
            return rejection

        # change=True — PATCH targets an existing object, so mirror
        # Django's change view (see detail.py for the rationale; a
        # consumer get_form override that branches on `change` must hit
        # its change-form path, not the default factory).
        form = model_admin.get_form(request, obj=obj, change=True)(
            data=form_data,
            files=files,
            instance=obj,
        )
        if not form.is_valid():
            return validation_failed(form_errors_to_envelope(form))

        # Shared create/update write pipeline (#55): form.save → save_model
        # → save_related → LogEntry → optional inlines, all atomic, with the
        # IntegrityError / inline-error / malformed-payload translation. The
        # change posture is ``change=True`` (save_model + log_change), which
        # mirrors Django's change view.
        result = save_through_admin(
            model_admin,
            request,
            form,
            change=True,
            inlines_payload=inlines_payload,
        )
        if isinstance(result, HttpResponse):
            return result
        instance = result

        response = JsonResponse(
            _build_payload(model, model_admin, instance, request, admin_site),
            status=200,
        )
        response["Cache-Control"] = "no-store"
        return response

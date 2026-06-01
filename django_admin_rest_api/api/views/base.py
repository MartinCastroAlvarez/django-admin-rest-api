"""Shared base view for the JSON API (#65).

Every API view subclasses :class:`BaseAPIView` instead of Django's bare
``django.views.generic.View`` so a request that uses an HTTP method the
view does not implement returns the package's canonical
``method_not_allowed`` JSON envelope — the same ``{"error": {"code", …}}``
shape every other error uses — instead of Django's default
``HttpResponseNotAllowed`` (a bare 405 with an HTML/empty body).

The ``method_not_allowed`` code is advertised in the OpenAPI schema
(``views/schema.py`` Error.code enum) and in ``docs/api-contract.md`` §1.1,
so a JSON client doing ``resp.json()["error"]["code"]`` on a 405 must get a
real envelope. The ``Allow`` header (which methods *are* permitted) is
preserved so the response is still a spec-compliant 405.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.generic import View


class BaseAPIView(View):
    """``View`` subclass that emits the JSON ``method_not_allowed`` envelope.

    Drop-in replacement for ``django.views.generic.View``: it changes only
    the disallowed-method response, leaving dispatch / ``http_method_names``
    semantics untouched.
    """

    def http_method_not_allowed(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Return the canonical 405 JSON envelope with the ``Allow`` header.

        Mirrors Django's own ``HttpResponseNotAllowed`` for the ``Allow``
        header (the list of permitted, upper-cased methods) but swaps the
        bare body for the package's uniform error envelope so JSON clients
        never crash on ``resp.json()`` for a 405.
        """
        # Django's own ``http_method_not_allowed`` builds the ``Allow`` header
        # from the view's permitted methods; reuse it (rather than the private
        # ``_allowed_methods``) so the header matches Django exactly, then swap
        # the bare body for the JSON envelope.
        allowed = super().http_method_not_allowed(request, *args, **kwargs)["Allow"]
        body = {
            "error": {
                "code": "method_not_allowed",
                "message": str(_("Method not allowed.")),
            }
        }
        response = JsonResponse(body, status=405)
        response["Allow"] = allowed
        # A method-routing decision is not cacheable per-resource.
        response["Cache-Control"] = "no-store"
        return response

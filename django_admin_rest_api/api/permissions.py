"""Permission helpers.

The package's default permission gate is:

    user.is_authenticated and user.is_active and user.is_staff
    and admin_site.has_permission(request)

Per-operation gates always go through the relevant
``ModelAdmin.has_*_permission(request, obj=None)`` method.

403 responses come in two flavours so the client can distinguish "you were
never authenticated" from "your session expired" (Issue #63):

- ``error.code = "forbidden"`` — generic permission denial; the client
  redirects to the configured login URL.
- ``error.code = "session_expired"`` — the request carried a session
  cookie but the resolved user is anonymous; the client shows a re-login
  modal that returns the user to the same page after sign-in.

See ``SECURITY.md`` §3 (rules 1 and 12) for the contract this enforces
and ``docs/api-contract.md`` §6 for the wire shape.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any
from typing import Final

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

from django_admin_rest_api.api.registry import get_admin_site

# Dedicated security logger (#67). Emits one structured record per authz
# denial / failed login so operators can alert on credential-stuffing,
# permission-probing, and IDOR-scan patterns from the package's own logs.
# Records carry only ``{user, path, method, decision}`` — never a password,
# never request-body PII. Operators wire it up via ``LOGGING`` under the
# ``django_admin_rest_api.security`` logger name.
security_logger = logging.getLogger("django_admin_rest_api.security")


def _user_id_or_anon(request: HttpRequest | None) -> str:
    """Return the request user's pk as a string, or ``"anon"``.

    Never returns the username / email — only the surrogate pk — so the
    log record carries no PII beyond an opaque identifier the operator can
    correlate against their own user table.
    """
    if request is None:
        return "anon"
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return str(getattr(user, "pk", "anon"))
    return "anon"


def log_security_event(request: HttpRequest | None, decision: str) -> None:
    """Emit one structured ``django_admin_rest_api.security`` record.

    ``decision`` is a short machine token (``forbidden``,
    ``session_expired``, ``login_failed``). The ``extra`` dict carries the
    structured fields so a JSON log formatter can index them; the message
    string itself is deliberately free of any user-supplied value.
    """
    method = getattr(request, "method", "-") if request is not None else "-"
    path = getattr(request, "path", "-") if request is not None else "-"
    security_logger.info(
        "security decision=%s user=%s method=%s path=%s",
        decision,
        _user_id_or_anon(request),
        method,
        path,
        extra={
            "user": _user_id_or_anon(request),
            "path": path,
            "method": method,
            "decision": decision,
        },
    )


def _user_is_active_staff(request: HttpRequest) -> bool:
    """Return True iff the request user is authenticated, active, and staff.

    The triple check is intentional and each part is load-bearing.

    - ``is_authenticated`` rejects ``AnonymousUser``.
    - ``is_active`` ensures a deactivated account loses access immediately;
      relying on ``is_staff`` alone would still let a disabled superuser
      through.
    - ``is_staff`` is the standard Django admin gate.

    ``getattr(user, "is_active", False)`` (rather than ``user.is_active``)
    is defensive: a custom user model might omit the attribute, and the
    safe default is "no".
    """
    user = getattr(request, "user", None)
    return bool(
        user is not None
        and user.is_authenticated
        and getattr(user, "is_active", False)
        and getattr(user, "is_staff", False)
    )


def is_admin_user(request: HttpRequest, admin_site: AdminSite | None = None) -> bool:
    """Return True iff the request may access the package's API.

    The package's default policy is staff-only (rule 1 in ``SECURITY.md``
    §3). ``AdminSite.has_permission`` is the consumer's escape hatch: if
    a consumer's custom site allows non-staff users to access the admin,
    this package follows that decision (``docs/api-contract.md`` §7).
    """
    if not _user_is_active_staff(request):
        return False
    site = admin_site if admin_site is not None else get_admin_site()
    return bool(site.has_permission(request))


# Error-envelope strings are wrapped in ``gettext_lazy`` (#73) so a non-English
# admin gets localized envelopes. ``JsonResponse``'s ``DjangoJSONEncoder``
# resolves the lazy proxy at serialization time against the request-active
# locale (activated by Django's ``LocaleMiddleware`` — see README/SECURITY).
# The machine-readable ``code`` stays a plain ASCII string (never translated).
_FORBIDDEN_BODY: Final[dict[str, dict[str, Any]]] = {
    "error": {"code": "forbidden", "message": _("You do not have permission.")}
}

_SESSION_EXPIRED_BODY: Final[dict[str, dict[str, Any]]] = {
    "error": {
        "code": "session_expired",
        "message": _("Your session has expired. Please sign in again."),
    }
}


def is_session_expired(request: HttpRequest) -> bool:
    """Detect "had a session cookie, but the user is now anonymous".

    The signal is conservative on purpose: we only flag expiry when
    the request carried a non-empty session cookie *and* the user
    resolved to anonymous (no ``is_authenticated``). Without that
    cookie there's nothing to expire — the user is just not signed
    in (the canonical anonymous case the contract has always handled).

    The check is read-only and never touches the session backend;
    it inspects the cookie name in ``settings.SESSION_COOKIE_NAME``
    and the resolved ``request.user`` only. Defensive fallback to
    ``False`` if any attribute is missing — the worst case is the
    client gets the generic ``"forbidden"`` envelope and falls back to
    its normal login-redirect path.
    """
    cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    cookie_value = request.COOKIES.get(cookie_name)
    if not cookie_value:
        return False
    user = getattr(request, "user", None)
    return bool(user is None or not getattr(user, "is_authenticated", False))


def forbidden_response(request: HttpRequest | None = None) -> HttpResponse:
    """Return the package's canonical 403 envelope.

    When ``request`` is provided and ``is_session_expired(request)``
    returns ``True`` (the request carried a session cookie but the
    user is anonymous), the body uses the ``session_expired`` error
    code so the client can render a re-login modal instead of a hard
    redirect. Without the argument, or when no expiry signal is
    detected, the body is the generic ``forbidden`` envelope.

    The body never includes data identifying the resource (per
    ``SECURITY.md`` §3 rule 12). For unauthenticated requests, callers
    may also want to redirect to ``settings.LOGIN_URL`` — that's a
    caller-level choice and not encoded here.
    """
    body = _FORBIDDEN_BODY
    decision = "forbidden"
    if request is not None and is_session_expired(request):
        body = _SESSION_EXPIRED_BODY
        decision = "session_expired"
    # Structured security log at the denial boundary (#67). Best-effort:
    # observability must never turn a 403 into a 500.
    with contextlib.suppress(Exception):  # pragma: no cover — logging must not break the response
        log_security_event(request, decision)
    response = JsonResponse(body, status=403)
    # Discourage caching of a permission decision.
    response["Cache-Control"] = "no-store"
    return response

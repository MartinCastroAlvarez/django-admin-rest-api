"""``python manage.py admin_rest_api_check`` — install smoke test (#37).

Walks the consumer's project and prints a single-screen health
summary:

- The configured ``ADMIN_SITE`` resolves to an ``AdminSite`` instance.
- The three required middleware classes are in ``settings.MIDDLEWARE``.
- Every registered ``ModelAdmin`` is listed with its action count
  (broken down by ``batch`` / ``detail`` target).

Exits ``0`` when everything looks healthy, non-zero with a clear
message when anything looks off — so this command can also live in
CI / a deploy preflight.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings as django_settings
from django.contrib.admin.sites import AdminSite
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils.module_loading import import_string

from django_admin_rest_api.api.actions_meta import _classify_action

logger = logging.getLogger(__name__)

REQUIRED_MIDDLEWARE: tuple[str, ...] = (
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
)


class Command(BaseCommand):
    """``admin_rest_api_check`` management command — install smoke test."""

    help = (
        "Smoke-test the django-admin-rest-api install: validates the configured "
        "ADMIN_SITE, required middleware, and lists every registered ModelAdmin "
        "with its action count."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Print the health summary and exit non-zero if anything is off."""
        problems: list[str] = []
        out = self.stdout.write
        ok = self.style.SUCCESS
        warn = self.style.WARNING
        err = self.style.ERROR

        # ── 1. Resolve ADMIN_SITE ─────────────────────────────────────────
        # Avoid importing conf at module-import time so Django can boot
        # this command file even when DJANGO_ADMIN_REST_API settings are
        # malformed (the error should surface in OUR output, not as an
        # ImportError when manage.py loads the command list).
        from django_admin_rest_api import conf

        dotted = conf.ADMIN_SITE
        out(f"\n  AdminSite path: {dotted}")
        try:
            site = import_string(dotted)
        except (ImportError, ValueError) as exc:
            problems.append(f"could not import ADMIN_SITE {dotted!r}: {exc}")
            out(err(f"    ✗ failed to import: {exc}"))
            site = None
        else:
            if not isinstance(site, AdminSite):
                problems.append(
                    f"ADMIN_SITE {dotted!r} resolves to a {type(site).__name__}, not an AdminSite"
                )
                out(err(f"    ✗ not an AdminSite (got {type(site).__name__})"))
                site = None
            else:
                out(ok("    ✓ resolves to an AdminSite"))

        # ── 2. Middleware presence ───────────────────────────────────────
        out("\n  Middleware:")
        configured = list(getattr(django_settings, "MIDDLEWARE", ()) or ())
        for required in REQUIRED_MIDDLEWARE:
            if required in configured:
                out(ok(f"    ✓ {required}"))
            else:
                problems.append(f"required middleware missing: {required}")
                out(warn(f"    ✗ MISSING: {required}"))

        # ── 3. Registered models + actions ───────────────────────────────
        if site is None:
            out("\n  Skipping admin-site walk because ADMIN_SITE didn't resolve.")
        else:
            registered = sorted(
                site._registry.items(),
                key=lambda kv: (kv[0]._meta.app_label, kv[0]._meta.model_name),
            )
            out(f"\n  Registered ModelAdmins ({len(registered)} total):")
            if not registered:
                out(warn("    (no models registered — nothing for the API to expose)"))
            for model, model_admin in registered:
                count = _action_count(model_admin)
                meta = model._meta
                out(
                    f"    • {meta.app_label}.{meta.model_name}  "
                    f"({count['total']} action(s); "
                    f"{count['batch']} batch, {count['detail']} detail)"
                )

        out("")
        if problems:
            raise CommandError(
                "django-admin-rest-api install has problems:\n  - " + "\n  - ".join(problems)
            )
        out(ok("OK — django-admin-rest-api install looks healthy."))


def _action_count(model_admin: Any) -> dict[str, int]:
    """Count batch / detail / total actions exposed by one ModelAdmin.

    Defensive: any exception from the admin's own ``get_actions`` or
    the classifier degrades to zeros so a single bad admin does not
    sink the whole report.
    """
    try:
        # NB: get_actions normally needs a request; ``None`` works for
        # most stock admins. A ModelAdmin that hard-requires a request
        # falls through to the zero counter via the except below.
        raw = model_admin.get_actions(None) or {}
    except Exception:  # noqa: BLE001 — best-effort: a single misbehaving admin
        # must not sink the whole smoke-test report. Kept broad on purpose;
        # logged so the operator can still find the offending admin.
        logger.warning(
            "get_actions failed for %s; reporting zero actions",
            type(model_admin).__name__,
            exc_info=True,
        )
        return {"batch": 0, "detail": 0, "total": 0}
    batch = detail = 0
    for _name, (callable_attr, _resolved_name, _desc) in raw.items():
        try:
            target = _classify_action(callable_attr)
        except Exception:  # noqa: BLE001 — best-effort report; default to batch
            logger.warning("classifying action %r failed; defaulting to batch", _name)
            target = "batch"
        if target == "detail":
            detail += 1
        else:
            batch += 1
    return {"batch": batch, "detail": detail, "total": batch + detail}

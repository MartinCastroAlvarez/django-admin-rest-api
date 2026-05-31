"""Startup-time validation for the django-admin-rest-api install.

Three checks register at app-ready time so common consumer mistakes
surface at ``manage.py runserver`` / ``manage.py check`` rather than
as a 500 on the first authenticated request.

- **W001** — settings ``DJANGO_ADMIN_REST_API`` dict-NAME typo
  (#31). Catches attributes like ``DJANGO_ADMIN_REST_API_CONFIG`` /
  ``DJANGO_ADMIN_REST_API_SETTINGS`` that look like the canonical
  dict but aren't read by the package.

- **E001** — ``ADMIN_SITE`` dotted path resolves to an ``AdminSite``
  (#32). Without this check a typo silently degrades every request
  to a 500 because :func:`get_admin_site` only resolves at
  request-time.

- **W002** — required Django middleware classes are in
  ``settings.MIDDLEWARE`` (#33). The package relies on CSRF,
  Session, and Auth middleware; a missing one silently degrades
  behavior (no CSRF check, no session, no ``request.user``). The
  check warns rather than errors because a consumer may have
  swapped in a drop-in equivalent — only they know.

Hooked from ``DjangoAdminRestApiConfig.ready`` so the registration
is the AppConfig's *only* import-time side effect beyond Django's
own discovery.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.contrib.admin.sites import AdminSite
from django.core.checks import Error
from django.core.checks import Tags
from django.core.checks import Warning as ChecksWarning
from django.core.checks import register
from django.utils.module_loading import import_string

_CANONICAL_SETTING: str = "DJANGO_ADMIN_REST_API"

REQUIRED_MIDDLEWARE: tuple[str, ...] = (
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
)


@register(Tags.compatibility)
def check_settings_dict_name(app_configs: Any, **kwargs: Any) -> list[Any]:
    """W001: warn on `DJANGO_ADMIN_REST_API_*` attribute typos (#31)."""
    suspects: list[str] = sorted(
        name
        for name in dir(django_settings)
        if name.startswith(_CANONICAL_SETTING) and name != _CANONICAL_SETTING
    )
    if not suspects:
        return []
    return [
        ChecksWarning(
            (
                f"settings attribute(s) {', '.join(repr(s) for s in suspects)} "
                f"look like a typo of {_CANONICAL_SETTING!r} — the package only "
                "reads the exact name and would otherwise silently use defaults."
            ),
            hint=f"Rename to the canonical dict: ``{_CANONICAL_SETTING} = {{...}}``.",
            obj=_CANONICAL_SETTING,
            id="django_admin_rest_api.W001",
        )
    ]


@register(Tags.compatibility)
def check_admin_site_resolves(app_configs: Any, **kwargs: Any) -> list[Any]:
    """E001: `ADMIN_SITE` must resolve to an `AdminSite` instance (#32)."""
    # Local import: ``conf`` reads from ``django.conf.settings`` lazily;
    # we want resolution to happen here, at app-ready, not at module
    # import time.
    from django_admin_rest_api import conf

    dotted = conf.ADMIN_SITE
    try:
        site = import_string(dotted)
    except (ImportError, ValueError) as exc:
        return [
            Error(
                (
                    f"DJANGO_ADMIN_REST_API['ADMIN_SITE'] = {dotted!r} could not "
                    f"be imported: {exc}"
                ),
                hint=(
                    "Use a dotted path to an `AdminSite` instance, e.g. "
                    "`'django.contrib.admin.site'` (the default) or "
                    "`'my_project.admin.site'`."
                ),
                obj="ADMIN_SITE",
                id="django_admin_rest_api.E001",
            )
        ]
    if not isinstance(site, AdminSite):
        return [
            Error(
                (
                    f"DJANGO_ADMIN_REST_API['ADMIN_SITE'] = {dotted!r} resolved "
                    f"to a {type(site).__name__}, not an `AdminSite` instance."
                ),
                hint=(
                    "The dotted path must point at an instance (not a class) "
                    "of `django.contrib.admin.sites.AdminSite`."
                ),
                obj="ADMIN_SITE",
                id="django_admin_rest_api.E001",
            )
        ]
    return []


@register(Tags.compatibility)
def check_required_middleware(app_configs: Any, **kwargs: Any) -> list[Any]:
    """W002: warn for each required Django middleware missing (#33)."""
    configured = list(getattr(django_settings, "MIDDLEWARE", ()) or ())
    out: list[Any] = []
    for required in REQUIRED_MIDDLEWARE:
        if required not in configured:
            out.append(
                ChecksWarning(
                    (
                        f"required middleware {required!r} is not in "
                        "`settings.MIDDLEWARE`."
                    ),
                    hint=(
                        "Add it to `MIDDLEWARE` (or document that you "
                        "swapped in a drop-in equivalent — this is a "
                        "warning, not an error)."
                    ),
                    obj="MIDDLEWARE",
                    id="django_admin_rest_api.W002",
                )
            )
    return out

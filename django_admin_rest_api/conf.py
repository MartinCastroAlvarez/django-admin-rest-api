"""Lazy settings wrapper for django_admin_rest_api.

All package settings live under a single optional dict
``settings.DJANGO_ADMIN_REST_API``. Defaults are applied lazily so that
adding the app to ``INSTALLED_APPS`` does not require a settings entry.

Usage in package code:

    from django_admin_rest_api import conf
    conf.MAX_PAGE_SIZE

Nothing in the package should read ``django.conf.settings.DJANGO_ADMIN_REST_API``
directly — go through this module so defaults are consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings as django_settings

DEFAULTS: dict[str, Any] = {
    # Dotted path to the ``AdminSite`` instance whose ``ModelAdmin``
    # registry the API mirrors. Default is the global ``django.contrib.admin.site``;
    # override to expose a custom AdminSite (multi-site setups, restricted
    # admins, etc.).
    "ADMIN_SITE": "django.contrib.admin.site",
    # The list page size derives from the model's
    # ``ModelAdmin.list_per_page`` (Django's changelist source of truth),
    # so the API pages like the HTML admin with no extra setting.
    # ``DEFAULT_PAGE_SIZE`` is the fallback only when ``list_per_page``
    # is missing / invalid. ``MAX_PAGE_SIZE`` always caps ``?page_size``
    # (a DoS guard).
    "DEFAULT_PAGE_SIZE": 25,
    "MAX_PAGE_SIZE": 200,
    # Cap on ``len(pks)`` in the actions runner body. Mirrors
    # ``MAX_PAGE_SIZE``'s posture for the list endpoint: a DoS guard
    # against a crafted POST that asks the runner to invoke an
    # expensive action across a very large selection. Tuneable per
    # project (raise it if you have legitimate workflows running
    # actions across larger selections). 0 disables the cap entirely.
    "MAX_ACTION_PKS": 5000,
    # Cap on the number of rows a single bulk-update (``PATCH .../bulk/``)
    # batch may carry. Mirrors ``MAX_ACTION_PKS``'s posture: a DoS guard
    # against a crafted batch that materialises thousands of forms in one
    # request. Defaults to ``MAX_PAGE_SIZE`` (so a "save the whole page"
    # workflow fits in one batch) — see ``_load``, which fills this in when
    # the consumer hasn't set it explicitly. Tuneable per project; 0
    # disables the cap entirely.
    "MAX_BULK_UPDATES": None,
    # When True, list responses include per-query timing in a debug
    # block. Off by default — only enable in development.
    "ENABLE_PROFILING": False,
}


@dataclass(frozen=True)
class _PackageSettings:
    """Resolved package settings (immutable snapshot)."""

    ADMIN_SITE: str = DEFAULTS["ADMIN_SITE"]
    DEFAULT_PAGE_SIZE: int = DEFAULTS["DEFAULT_PAGE_SIZE"]
    MAX_PAGE_SIZE: int = DEFAULTS["MAX_PAGE_SIZE"]
    MAX_ACTION_PKS: int = DEFAULTS["MAX_ACTION_PKS"]
    MAX_BULK_UPDATES: int = DEFAULTS["MAX_PAGE_SIZE"]
    ENABLE_PROFILING: bool = DEFAULTS["ENABLE_PROFILING"]


def _load() -> _PackageSettings:
    """Merge the consumer's overrides with ``DEFAULTS``.

    Unknown keys raise ``ValueError`` so a typo in
    ``settings.DJANGO_ADMIN_REST_API`` is caught at startup rather than
    silently ignored.
    """
    user_overrides = getattr(django_settings, "DJANGO_ADMIN_REST_API", {}) or {}
    merged = {**DEFAULTS, **user_overrides}
    unknown = set(merged) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown DJANGO_ADMIN_REST_API keys: " + ", ".join(sorted(unknown)))
    # Single-source the bulk cap (#69): when the consumer hasn't set
    # ``MAX_BULK_UPDATES`` explicitly it tracks ``MAX_PAGE_SIZE`` (so a
    # "save the whole page" workflow always fits in one batch, and lowering
    # MAX_PAGE_SIZE for DoS reasons tightens the bulk cap too).
    if merged["MAX_BULK_UPDATES"] is None:
        merged["MAX_BULK_UPDATES"] = merged["MAX_PAGE_SIZE"]
    return _PackageSettings(**merged)


_cached: _PackageSettings | None = None


def __getattr__(name: str) -> Any:  # pragma: no cover — thin shim
    """Module-level ``__getattr__`` (PEP 562) so callers can write
    ``from django_admin_rest_api import conf`` and then
    ``conf.MAX_PAGE_SIZE`` without a separate accessor.
    """
    global _cached
    if _cached is None:
        _cached = _load()
    return getattr(_cached, name)

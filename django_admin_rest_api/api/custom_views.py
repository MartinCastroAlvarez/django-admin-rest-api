"""Surface a ``ModelAdmin.get_urls()`` custom view as a wire descriptor (#439).

A ``ModelAdmin`` may register bespoke admin pages via ``get_urls()`` — an
import wizard, a report, a "download schema.sql" link, etc. The SPA cannot
render the consumer's Django template, so it links out to the
legacy-admin-rendered page (``CustomViewsMenu`` opens it in a new tab). This
module is the **producer** half of that contract: it walks ``get_urls()``,
drops the five stock CRUD routes Django adds itself, and returns the
consumer's extra routes as ``{name, label, url, level}`` entries.

Wire contract: ``docs/api-contract.md`` §2 (registry, changelist-level) and
§4 (detail, object-level). The client type is ``CustomView`` in the React
package's ``contract.ts``.

Two levels, by whether the route captures an object id:

- ``level: "object"``  — the route captures ``object_id`` (Django's
  convention for per-object admin views). Reversed with the object's pk;
  surfaced on the **detail** response only.
- ``level: "changelist"`` — a model-wide page (no object capture). Reversed
  with no args; surfaced on the **registry** model entry.

Everything here is best-effort: a consumer ``get_urls()`` override may raise,
a custom route may fail to reverse (extra captures we can't fill), or a name
may collide. Any such entry is skipped — a broken custom view must never 500
the registry or detail endpoint or drop the stock payload.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Final

from django.contrib.admin import AdminSite
from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.urls import reverse

logger = logging.getLogger(__name__)

# The five route name suffixes ``ModelAdmin.get_urls()`` adds for stock CRUD.
# Everything else a consumer registers is a custom view.
_STOCK_SUFFIXES: Final[frozenset[str]] = frozenset(
    {"changelist", "add", "history", "delete", "change"}
)

# Capture-group names that mark a route as object-level (Django uses
# ``object_id``; ``pk`` / ``id`` are accepted for hand-rolled patterns).
_OBJECT_CAPTURES: Final[frozenset[str]] = frozenset({"object_id", "pk", "id"})


def custom_views_payload(
    model_admin: ModelAdmin,
    admin_site: AdminSite,
    *,
    level: str,
    obj: Model | None = None,
) -> list[dict[str, Any]]:
    """Custom ``get_urls()`` routes for one model, filtered to ``level``.

    ``level="changelist"`` returns the model-wide pages (registry use);
    ``level="object"`` returns the per-object pages reversed with ``obj.pk``
    (detail use, so ``obj`` is required there). Stock CRUD routes are always
    excluded. Returns ``[]`` on any failure — never raises.

    No ``request`` is taken: like the legacy admin, every custom-view link a
    consumer registers via ``get_urls()`` is surfaced; the linked view does
    its own permission gating (it is wrapped in ``self.admin_view``).
    """
    if level == "object" and obj is None:
        return []

    meta = model_admin.model._meta
    prefix = f"{meta.app_label}_{meta.model_name}_"
    site_namespace = admin_site.name

    # Best-effort: a consumer ``get_urls`` override can raise; an empty list
    # degrades gracefully (no custom views surfaced) rather than 500ing the
    # caller. Kept broad on purpose; logged.
    try:
        patterns = list(model_admin.get_urls())
    except Exception:
        logger.warning("get_urls() raised for %s; omitting custom views", meta.label, exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in patterns:
        name = getattr(pattern, "name", None)
        if not name or not name.startswith(prefix) or name in seen:
            continue
        suffix = name[len(prefix) :]
        if suffix in _STOCK_SUFFIXES:
            continue

        entry_level = "object" if _is_object_route(pattern) else "changelist"
        if entry_level != level:
            continue

        url = _reverse_view(site_namespace, name, entry_level, obj)
        if url is None:
            continue

        seen.add(name)
        out.append(
            {
                "name": name,
                "label": _label_for(pattern, suffix),
                "url": url,
                "level": entry_level,
            }
        )
    return out


def _is_object_route(pattern: Any) -> bool:
    """Whether the route captures an object id (→ object-level)."""
    # ``URLPattern.pattern.regex.groupindex`` maps each named capture to its
    # group number — the most robust way to read the route's parameters
    # across RoutePattern (path()) and RegexPattern (re_path()).
    try:
        captures = set(pattern.pattern.regex.groupindex.keys())
    except Exception:  # pragma: no cover — defensive; pattern shape is stable
        return False
    return bool(captures & _OBJECT_CAPTURES)


def _reverse_view(namespace: str, name: str, level: str, obj: Model | None) -> str | None:
    """Reverse the admin route, with the pk for object-level. ``None`` on failure."""
    # Best-effort: a custom route may declare captures beyond ``object_id``
    # that we can't fill, or the admin site may not be mounted under the
    # expected namespace. Skip the entry rather than 500. Kept broad; logged.
    try:
        if level == "object":
            return reverse(f"{namespace}:{name}", args=[obj.pk])  # type: ignore[union-attr]
        return reverse(f"{namespace}:{name}")
    except Exception:
        logger.warning("could not reverse custom view %r; omitting", name, exc_info=True)
        return None


def _label_for(pattern: Any, suffix: str) -> str:
    """Human label: the view's ``short_description`` if set, else humanized name."""
    # ``self.admin_view(self.my_view)`` wraps the callable via
    # ``functools.update_wrapper``, which copies ``__dict__`` — so a
    # ``short_description`` attribute set on the bound method propagates to
    # the wrapper and to ``__wrapped__``. Check both, then fall back.
    callback = getattr(pattern, "callback", None)
    for target in (callback, getattr(callback, "__wrapped__", None)):
        description = getattr(target, "short_description", None)
        if description:
            return str(description)
    return suffix.replace("_", " ").strip().title()

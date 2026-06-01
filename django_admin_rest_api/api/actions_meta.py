"""Admin-action introspection helpers (lower layer, no view imports).

This module holds the action *metadata* helpers — signature
classification and the ``actions`` payload builder — that several
layers need (``registry.build_registry_payload``, the list/detail
views, and the ``ActionView`` runner itself).

It deliberately depends only on Django and the stdlib so that
``registry.py`` can import :func:`actions_payload` at module top level.
Previously ``registry`` lazy-imported ``actions_payload`` from
``views.actions`` to dodge a ``registry ↔ views.actions`` import cycle;
hoisting these pure helpers down here removes the cycle entirely (#55).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from django.contrib.admin.utils import model_format_dict
from django.db.models import Model
from django.db.models import QuerySet
from django.http import HttpRequest

# Signature inspection — used to decide whether a registered
# ``ModelAdmin.actions`` callable expects a ``QuerySet`` (changelist
# / "batch" shape) or a single object id (detail-page shape).
#
# Two signals, in order:
#
#   1. Parameter name. Stock Django actions use ``queryset``; consumers
#      writing detail-shaped actions tend to use ``obj_id`` / ``object_id``
#      / ``pk`` / ``id``. Name match is cheap and reliable.
#   2. Type annotation. Falls back to ``QuerySet`` / ``Model`` /
#      ``str`` / ``int`` annotations when names are ambiguous.
#
# Anything not classified as ``detail`` defaults to ``batch`` — that
# preserves stock Django's contract (every action takes a queryset).
_DETAIL_PARAM_NAMES: frozenset[str] = frozenset({"obj_id", "object_id", "pk", "id", "object_pk"})
_BATCH_PARAM_NAMES: frozenset[str] = frozenset({"queryset", "qs"})


def _classify_action(callable_attr: Callable[..., Any]) -> str:
    """Return ``"batch"`` or ``"detail"`` for one action callable.

    ``"batch"`` (default): signature follows Django's stock action
    contract ``(modeladmin, request, queryset)``. Runner calls it once
    with the user-narrowed ``QuerySet``.

    ``"detail"``: signature takes a single object id, e.g.
    ``(self, request, obj_id: str)``. Runner calls it once per pk in the
    request, passing ``str(pk)`` rather than the queryset.

    Inspection is best-effort: a callable whose signature can't be
    introspected (C-extension, builtins, exotic decorators) falls back
    to ``"batch"`` so it stays compatible with stock Django.
    """
    try:
        params = list(inspect.signature(callable_attr).parameters.values())
    except (TypeError, ValueError):
        return "batch"

    # First pass: decide by parameter NAME. Cheap and authoritative
    # whenever the consumer uses one of the conventional names.
    for param in params:
        name = param.name.lower()
        if name in _DETAIL_PARAM_NAMES:
            return "detail"
        if name in _BATCH_PARAM_NAMES:
            return "batch"

    # Second pass: decide by type ANNOTATION when names didn't.
    for param in params:
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            continue
        if annotation in (str, int):
            return "detail"
        try:
            if isinstance(annotation, type):
                if issubclass(annotation, QuerySet):
                    return "batch"
                if issubclass(annotation, Model):
                    return "detail"
        except TypeError:
            # ``issubclass`` on a non-class (e.g. ``QuerySet[Foo]``
            # generic) raises — skip and keep looking.
            continue

    return "batch"


def actions_payload(model_admin: Any, request: HttpRequest) -> list[dict[str, Any]]:
    """Build the ``actions`` block of the list / detail / registry response.

    Each entry is
    ``{name, label, description, requires_confirmation, target}``.

    - ``requires_confirmation`` is conservative: ``True`` only when the
      action's docstring or short_description hints at destructiveness
      (substring match on ``delete``). The client may always render a
      confirmation step regardless — this hint is a UX optimisation.
    - ``target`` is ``"batch"`` or ``"detail"``, derived by inspecting
      the callable's signature (:func:`_classify_action`). The SPA uses
      it to decide whether to render the action on the changelist (with
      multi-select) or on the single-object detail page. The same JSON
      runner serves both shapes — only the call shape inside the runner
      differs.
    """
    raw = model_admin.get_actions(request) or {}
    # Django's built-in `delete_selected` (and any action whose
    # `short_description` uses the admin's `%(verbose_name)s` /
    # `%(verbose_name_plural)s` placeholders) ships a *format string*,
    # not a finished label — Django interpolates it at render time via
    # `model_format_dict(opts)`. Do the same here so the client shows
    # "Delete selected files", never the raw "%(verbose_name_plural)s".
    fmt = model_format_dict(model_admin.model._meta)
    out: list[dict[str, Any]] = []
    for name, (_callable, _resolved_name, description) in raw.items():
        raw_label = str(description) if description else name
        try:
            label = raw_label % fmt
        except (KeyError, ValueError, TypeError):
            # Not a %-format string, or references a key we don't
            # provide — surface the label verbatim rather than crashing.
            label = raw_label
        requires_conf = "delete" in (label.lower() + " " + name.lower())
        out.append(
            {
                "name": name,
                "label": label,
                "description": label,
                "requires_confirmation": requires_conf,
                "target": _classify_action(_callable),
            }
        )
    return out

"""``GET /api/v1/<app>/<model>/<pk>/`` — single-object detail view.

Wire contract: ``docs/api-contract.md`` §4.

Hard rules (`SECURITY.md` §3):

- Rule 1:  Staff + ``AdminSite.has_permission`` gate.
- Rule 3:  Model resolved through ``admin.site._registry`` (B-7).
- Rule 5:  ``has_view_permission(request, obj)`` per-object gate.
- Rule 6:  Fields come from ``ModelAdmin.get_form(request, obj)`` /
           ``get_fields`` / ``get_readonly_fields`` / ``get_exclude``.
           Sensitive-name denylist applied on top
           (``docs/api-contract.md`` §4).
- Rule 10: Queryset starts at ``ModelAdmin.get_queryset(request)`` —
           never ``Model.objects.all()``.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any
from typing import Final

from django.contrib.admin.options import ModelAdmin
from django.contrib.admin.utils import label_for_field
from django.contrib.admin.utils import lookup_field
from django.db.models import FileField
from django.db.models import ForeignKey
from django.db.models import ManyToManyField
from django.db.models import Model
from django.forms.widgets import PasswordInput
from django.forms.widgets import Textarea
from django.forms.widgets import TextInput
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse

from django_admin_rest_api.api.actions_meta import actions_payload
from django_admin_rest_api.api.inlines import inlines_payload
from django_admin_rest_api.api.permissions import forbidden_response
from django_admin_rest_api.api.permissions import is_admin_user
from django_admin_rest_api.api.registry import get_admin_site
from django_admin_rest_api.api.registry import model_permissions
from django_admin_rest_api.api.registry import password_change_meta
from django_admin_rest_api.api.registry import resolve_model
from django_admin_rest_api.api.registry import save_options
from django_admin_rest_api.api.serializers import field_metadata
from django_admin_rest_api.api.serializers import filter_sensitive
from django_admin_rest_api.api.serializers import is_sensitive_field_name
from django_admin_rest_api.api.serializers import label_for
from django_admin_rest_api.api.serializers import safe_get_field
from django_admin_rest_api.api.serializers import serialize_fk_value
from django_admin_rest_api.api.serializers import serialize_value
from django_admin_rest_api.api.views.base import BaseAPIView
from django_admin_rest_api.api.writes import load_object_or_none
from django_admin_rest_api.api.writes import not_found_response

logger = logging.getLogger(__name__)


class DetailView(BaseAPIView):
    """``GET /api/v1/<app_label>/<model_name>/<pk>/`` — single object."""

    http_method_names = ["get"]

    def get(
        self,
        request: HttpRequest,
        app_label: str,
        model_name: str,
        pk: str,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponse:
        """Return the full descriptor for one object (contract §4).

        Gates, in order:

        1. ``is_admin_user`` — 403 if not authenticated active staff.
        2. ``resolve_model`` — 404 if model unknown or unviewable.
        3. ``load_object_or_none`` — 404 if pk doesn't resolve under
           the admin's queryset (rule 10) or parse-fails.
        4. ``has_view_permission(request, obj)`` — per-object gate
           (rule 5); 403 once we know the object exists but the user
           may not see *this* row.

        The payload includes the visible field set, fieldsets, the
        four ``has_*_permission`` booleans, and a friendly label.
        Excluded / readonly / sensitive-named fields are dropped by
        the visibility filter (defense in depth on top of the admin
        form).
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

        if not model_admin.has_view_permission(request, obj):
            return forbidden_response(request)

        payload = _build_payload(model, model_admin, obj, request, admin_site)
        response = JsonResponse(payload, status=200)
        # No-store: per-user, permission-gated payload must never be
        # cached by intermediate proxies or the browser
        # (``docs/api-contract.md`` §1.2).
        response["Cache-Control"] = "no-store"
        return response


# --------------------------------------------------------------------------- #
# Payload assembly                                                            #
# --------------------------------------------------------------------------- #
def _build_payload(
    model: type[Model],
    model_admin: ModelAdmin,
    obj: Model,
    request: HttpRequest,
    admin_site: Any,
) -> dict[str, Any]:
    """Compose the full detail response body (contract §4)."""
    visible_names = _visible_field_names(model_admin, request, obj)
    return {
        "app_label": model._meta.app_label,
        "model_name": model._meta.model_name,
        "pk": obj.pk,
        "label": label_for(obj),
        "permissions": model_permissions(model_admin, request),
        "save_options": save_options(model_admin, request, obj),
        "password_change": password_change_meta(model_admin, request, obj),
        "fieldsets": _fieldsets_payload(model_admin, request, obj, visible_names),
        "fields": _fields_payload(model, model_admin, obj, request, visible_names, admin_site),
        "inlines": inlines_payload(model_admin, obj, request, admin_site),
        "view_on_site_url": _view_on_site_url(model_admin, obj),
        # Object-page actions (#603 — revised). The SPA's detail page
        # renders one button per entry, click → POST to the existing
        # `<app>/<model>/actions/<name>/` runner with `pks=[<this pk>]`.
        # SOURCE OF TRUTH: the consumer's `ModelAdmin.actions` (the
        # stock Django admin action API: `@admin.action(description=...)`).
        # NO django-object-actions integration, NO change_actions
        # redeclaration — one place to declare an action, two places it
        # shows up (changelist + detail). Same descriptor shape the
        # list response surfaces (`{name, label, description,
        # requires_confirmation}`).
        "object_actions": actions_payload(model_admin, request),
        # empty_value_display (#251): the admin's configured placeholder for
        # empty/null values (ModelAdmin override → AdminSite default "-"), so
        # the client renders it instead of a hardcoded em-dash. ``str()`` keeps
        # it a plain string on the wire (it's a SafeString in Django).
        "empty_value_display": str(model_admin.get_empty_value_display()),
    }


def _view_on_site_url(model_admin: ModelAdmin, obj: Model) -> str | None:
    """The "View on site" URL for this object, or ``None`` (Issue #307).

    Mirrors Django's change-form "View on site" affordance:

    - ``ModelAdmin.view_on_site`` is falsy → no link.
    - it's a callable → ``view_on_site(obj)`` (the consumer builds the URL).
    - it's ``True`` and the model defines ``get_absolute_url`` → that URL.

    Unlike Django's ``get_view_on_site_url`` we resolve ``get_absolute_url``
    directly rather than routing through the ``admin:view_on_site`` shortcut
    redirect — that shortcut lives on ``django.contrib.admin``'s URLConf,
    which a consumer who has fully swapped out the legacy admin may no
    longer mount. Any error degrades to ``None`` (a broken consumer
    ``get_absolute_url`` must never 500 the detail endpoint).
    """
    try:
        view_on_site = getattr(model_admin, "view_on_site", False)
        if not view_on_site or obj is None:
            return None
        if callable(view_on_site):
            url = view_on_site(obj)
            return str(url) if url else None
        get_absolute_url = getattr(obj, "get_absolute_url", None)
        if callable(get_absolute_url):
            url = get_absolute_url()
            return str(url) if url else None
    except Exception:
        # Best-effort: a consumer ``view_on_site`` callable or the model's
        # ``get_absolute_url`` may raise; omit the link rather than 500 the
        # detail view. Kept broad on purpose; logged.
        logger.warning("view_on_site / get_absolute_url failed; omitting link", exc_info=True)
        return None
    return None


def _visible_field_names(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Model | None,
) -> list[str]:
    """Field names the detail response may surface for this object.

    This is the *read*-visible set: it includes readonly fields (so
    the UI can render them) but drops admin-excluded fields and any
    name matching the sensitive denylist. The *writable* set (for
    POST/PATCH) lives in ``writes.writable_field_names`` and is
    narrower.
    """
    declared = list(model_admin.get_fields(request, obj) or ())
    excluded = set(model_admin.get_exclude(request, obj) or ())
    visible = [
        name
        for name in declared
        if isinstance(name, str) and name not in excluded and not is_sensitive_field_name(name)
    ]
    return filter_sensitive(visible)


def _fieldsets_payload(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Model | None,
    visible_names: list[str],
) -> list[dict[str, Any]]:
    """Honour ``ModelAdmin.get_fieldsets`` (with a flat fallback).

    Fieldset entries that the admin lists but the visibility filter
    drops are silently removed from the group. An empty result is
    returned as the single "default" group so the client always has at
    least one section to render.
    """
    # Best-effort: a consumer ``get_fieldsets`` override may raise; fall back
    # to the flat default below rather than 500 the detail view. Kept broad
    # on purpose; logged.
    try:
        raw = model_admin.get_fieldsets(request, obj) or ()
    except Exception:
        logger.warning("get_fieldsets failed; using flat fallback", exc_info=True)
        raw = ()
    if not raw:
        return [
            {"title": None, "fields": visible_names, "field_rows": [[n] for n in visible_names]}
        ]

    visible_set = set(visible_names)
    payload: list[dict[str, Any]] = []
    for title, opts in raw:
        # Preserve Django's multi-field-row grouping (#382): a fieldset
        # ``fields`` entry that is a tuple/list — e.g. ``(("first", "last"),
        # "email")`` — is one display row. ``field_rows`` keeps that shape
        # (each inner list = one row, after the visibility filter); the flat
        # ``fields`` is kept for back-compat as the row-flattened list.
        field_rows: list[list[str]] = []
        for entry in opts.get("fields", ()):
            row = [
                sub
                for sub in (entry if isinstance(entry, list | tuple) else (entry,))
                if sub in visible_set
            ]
            if row:
                field_rows.append(row)
        fields = [sub for row in field_rows for sub in row]
        if fields:
            # Carry the fieldset's ``classes`` (e.g. ``collapse`` / ``wide``)
            # and ``description`` so the client can render a collapsible section
            # and show the section help text (Django change-form parity).
            classes = [str(c) for c in (opts.get("classes") or ())]
            description = opts.get("description")
            payload.append(
                {
                    "title": title,
                    "fields": fields,
                    "field_rows": field_rows,
                    "classes": classes,
                    "description": str(description) if description else None,
                }
            )
    return payload or [
        {"title": None, "fields": visible_names, "field_rows": [[n] for n in visible_names]}
    ]


def _fields_payload(
    model: type[Model],
    model_admin: ModelAdmin,
    obj: Model,
    request: HttpRequest,
    visible_names: list[str],
    admin_site: Any,
) -> dict[str, dict[str, Any]]:
    """Build the per-field descriptor mapping (contract §4 ``fields``)."""
    readonly = set(model_admin.get_readonly_fields(request, obj) or ())
    # ``change=True`` — the detail view is always for an EXISTING object,
    # so we mirror exactly how Django's change view calls ``get_form``
    # (``ModelAdmin._changeform_view`` passes ``change=not add``). A
    # consumer ``get_form`` override commonly branches on ``change`` to
    # return a change-specific form (one whose Meta omits form-only
    # fields like ``admin_override``). Calling without ``change=True``
    # makes that override fall through to the default factory, which
    # then raises ``FieldError`` on the form-only field and 500s the
    # detail endpoint.
    form = model_admin.get_form(request, obj=obj, change=True)(instance=obj)

    out: dict[str, dict[str, Any]] = {}
    for name in visible_names:
        out[name] = _descriptor_for(
            model=model,
            model_admin=model_admin,
            obj=obj,
            name=name,
            form=form,
            is_readonly=name in readonly,
            admin_site=admin_site,
            request=request,
        )
    return out


def _is_autocomplete_field(
    model_admin: ModelAdmin,
    model: type[Model],
    name: str,
    request: HttpRequest,
    admin_site: Any,
) -> bool:
    """Whether ``name`` should render as an autocomplete typeahead (#72).

    True iff the field is a relation listed in
    ``ModelAdmin.get_autocomplete_fields(request)`` AND the related model's
    admin (on this site) declares ``search_fields`` — Django's own
    requirement for the autocomplete endpoint to return results. Without a
    searchable target admin the typeahead has nothing to query, so the
    client should fall back to a plain select. Best-effort: any resolution
    error degrades to ``False`` (no hint).
    """
    try:
        autocomplete = set(model_admin.get_autocomplete_fields(request) or ())
    except Exception:  # pragma: no cover — admin author error
        return False
    if name not in autocomplete:
        return False
    field = safe_get_field(model, name)
    related_model = getattr(getattr(field, "remote_field", None), "model", None)
    if related_model is None:
        return False
    target_admin = getattr(admin_site, "_registry", {}).get(related_model)
    if target_admin is None:
        return False
    return bool(getattr(target_admin, "search_fields", None))


def _descriptor_for(
    *,
    model: type[Model],
    model_admin: ModelAdmin,
    obj: Model,
    name: str,
    form: Any,
    is_readonly: bool,
    admin_site: Any,
    request: HttpRequest,
) -> dict[str, Any]:
    """Per-field descriptor for one ``visible_names`` entry."""
    model_field = safe_get_field(model, name)
    if model_field is None:
        # Form-extra fields (#606): a ModelAdmin's custom Form may
        # declare extra fields that are NOT on the model (e.g.
        # `email = forms.EmailField()` on a Profile form when `email`
        # lives on the related `User`). Django renders these correctly
        # in the legacy admin via `form.fields[name]`. Mirror that
        # behaviour: if the form has a field, build the descriptor
        # from it. The fallback to `_readonly_callable_descriptor`
        # stays for genuinely-no-form callables (`@admin.display`
        # methods etc., the original path).
        form_field = form.fields.get(name) if form is not None else None
        if form_field is not None:
            return _form_extra_field_descriptor(
                model_admin=model_admin,
                model=model,
                name=name,
                form_field=form_field,
                is_readonly=is_readonly,
            )
        return _readonly_callable_descriptor(model_admin, model, obj, name)

    if isinstance(model_field, ForeignKey):
        value: Any = serialize_fk_value(
            getattr(obj, name, None), admin_site=admin_site, request=request
        )
    elif isinstance(model_field, ManyToManyField):
        # M2M (Issue #55): serialise as a list of ``{id, label}``
        # envelopes. The related manager is iterable on a saved row;
        # unsaved rows (e.g. during ``obj=None`` create) have no
        # related set, so default to an empty list.
        try:
            related = list(getattr(obj, name).all())
        except (ValueError, AttributeError):
            related = []
        value = [serialize_fk_value(r, admin_site=admin_site, request=request) for r in related]
    elif isinstance(model_field, FileField):
        # FileField / ImageField (Issue #57): serialise as a
        # ``{name, url, size}`` envelope. ``None`` when the field is
        # empty. ``url`` defers to the consumer's storage backend so
        # signed-URL backends (S3, GCS) work without package
        # changes; ``size`` is best-effort (some storage backends
        # don't expose it cheaply, so we swallow exceptions).
        value = _serialize_file_value(getattr(obj, name, None))
    else:
        # Forward the model_field so consumer-registered custom
        # serializers (see #60 / ``register_field_type``) take
        # precedence over the default Python-type dispatch.
        value = serialize_value(getattr(obj, name, None), field=model_field)

    form_field = form.fields.get(name)
    required = bool(form_field.required) if form_field is not None else False
    help_text = getattr(model_field, "help_text", "") or (
        form_field.help_text if form_field is not None else ""
    )

    descriptor = field_metadata(
        model_field,
        label=_field_label(model_admin, model, name),
        required=required,
        readonly=is_readonly,
        help_text=str(help_text),
        value=value,
    )
    # radio_fields (#251): when the admin lists this choice/FK field in
    # ``radio_fields``, hint the client to render radios instead of a select.
    # Presentational only — no permission/value change.
    if name in (getattr(model_admin, "radio_fields", None) or {}):
        descriptor["widget"] = "radio"
    # raw_id_fields (#251): FK/M2M fields the admin lists here render as a
    # pk input + lookup instead of a full select (for high-cardinality
    # relations). ``elif`` so ``radio_fields`` wins if a field is in both.
    elif name in (getattr(model_admin, "raw_id_fields", None) or ()):
        descriptor["widget"] = "raw_id"
    # filter_horizontal / filter_vertical (#627): M2M fields the admin
    # lists here render as Django's two-pane shuttle widget in the
    # legacy HTML admin. Emit the orientation hint so the SPA can
    # render a searchable two-pane control instead of a single-list
    # checkbox bank (which doesn't scale past ~50 options). ``elif``
    # so ``raw_id_fields`` wins on the same field (operator opted out
    # of large-set widgets entirely).
    elif name in (getattr(model_admin, "filter_horizontal", None) or ()):
        descriptor["widget"] = "shuttle_h"
    elif name in (getattr(model_admin, "filter_vertical", None) or ()):
        descriptor["widget"] = "shuttle_v"
    # autocomplete_fields (#72): a high-cardinality FK/M2M the admin lists in
    # ``autocomplete_fields`` should render as a typeahead backed by the
    # ``/<app>/<model>/autocomplete/`` endpoint, not a full select. Only hint
    # it when the *target* admin actually declares ``search_fields`` (Django's
    # own requirement for autocomplete to work) — otherwise the typeahead has
    # nothing to search and the client should fall back to a plain select.
    # ``elif`` so the earlier raw_id / shuttle wins if a field is in both.
    elif _is_autocomplete_field(model_admin, model, name, request, admin_site):
        descriptor["widget"] = "autocomplete"
    # formfield_overrides (#446): the bound form field's widget already
    # reflects the admin's ``formfield_overrides`` /
    # ``formfield_for_dbfield`` — Django applied them in ``get_form``.
    # Honour the one override the client can act on with the existing type
    # vocabulary: a single-line string promoted to a ``Textarea`` becomes
    # the multi-line ``text`` type (rendered as a ``<textarea>``), and a
    # multi-line ``text`` forced to a single-line ``TextInput`` collapses
    # back to ``string``. Other widget overrides (date pickers, FK
    # autocomplete) the client already renders from the field type. Choice
    # fields are untouched — their ``choice`` type wins above.
    _apply_widget_override(descriptor, form_field)
    return descriptor


def _apply_widget_override(descriptor: dict[str, Any], form_field: Any) -> None:
    """Reconcile the descriptor type with the bound form field's widget.

    Reuses the form widget (source of truth) so ``formfield_overrides``
    has a visible effect, mapping only to the existing ``string`` /
    ``text`` vocabulary so no new wire type is introduced (#446).

    A ``PasswordInput`` override is handled first and separately (#504):
    it is a security boundary, not a layout hint. Django's admin renders
    a ``PasswordInput`` with ``render_value=False`` by default, so the
    stored value is never echoed back into the page. The client reads its
    value over the wire, so the equivalent is to **redact the value from
    the payload** unless the admin explicitly opted into echoing it
    (``render_value=True``), and to hint the client to mask the input
    (``widget: "password"``). Without this, a secret stored on a
    ``CharField`` the admin masked with ``PasswordInput`` would be sent
    as plaintext in the detail JSON.
    """
    if form_field is None:
        return
    widget = getattr(form_field, "widget", None)
    if widget is None:
        return
    if isinstance(widget, PasswordInput):
        descriptor["widget"] = "password"
        if not getattr(widget, "render_value", False):
            descriptor["value"] = None
        return
    if descriptor["type"] == "string" and isinstance(widget, Textarea):
        descriptor["type"] = "text"
    elif (
        descriptor["type"] == "text"
        and isinstance(widget, TextInput)
        and not isinstance(widget, Textarea)
    ):
        descriptor["type"] = "string"

    # Custom widget detection (#625). If the bound form field's widget
    # class lives OUTSIDE Django's own ``django.*`` widget tree, it
    # came from the consumer's own code — via ``formfield_overrides``,
    # ``formfield_for_dbfield``, a custom Form class, or a third-party
    # widget library. Emit a ``widget: "custom"`` hint plus the
    # widget's dotted Python path so SPA-side plugin registrations
    # can dispatch on it. A subclass of a stock widget (e.g.
    # ``MyAttrInput(TextInput)``) stays NOT marked custom — the
    # default render path for the field's ``type`` still works for it
    # since the attribute-level differences don't change the wire
    # contract.
    #
    # The hint is only set when no earlier branch (``radio_fields`` /
    # ``raw_id_fields`` / ``filter_horizontal`` / ``PasswordInput``)
    # already claimed ``descriptor["widget"]`` — those wins are
    # specific opt-ins that the operator chose explicitly and that the
    # SPA already has dedicated renders for.
    if "widget" not in descriptor:
        widget_module = type(widget).__module__
        if not widget_module.startswith("django."):
            # Stock Django widgets we'd accidentally catch via
            # subclassing chains (e.g. an admin widget subclassing a
            # stock one but living in ``django.contrib.admin``) are
            # excluded by the ``django.`` prefix above. Truly custom
            # widget classes (from a consumer's app or a third-party
            # package) flow through here.
            descriptor["widget"] = "custom"
            descriptor["widget_class"] = f"{widget_module}.{type(widget).__name__}"


# Form-field-class → wire type. Mirrors `_TYPE_BY_INTERNAL` in
# serializers.py but for Django's `forms.*` field classes (used by a
# ModelAdmin's custom Form to declare non-model fields, #606).
# Same vocabulary so the client doesn't need to know whether a
# field came from the model or from a form-only declaration.
_FORM_FIELD_TYPE: Final[dict[str, str]] = {
    "BooleanField": "boolean",
    "NullBooleanField": "boolean",
    "CharField": "string",
    "EmailField": "email",
    "URLField": "url",
    "SlugField": "slug",
    "UUIDField": "uuid",
    "IntegerField": "integer",
    "FloatField": "float",
    "DecimalField": "decimal",
    "DateField": "date",
    "DateTimeField": "datetime",
    "TimeField": "time",
    "DurationField": "duration",
    "FileField": "file",
    "ImageField": "image",
    "FilePathField": "filepath",
    "GenericIPAddressField": "ip",
    "JSONField": "json",
    "ChoiceField": "choice",
    "TypedChoiceField": "choice",
    "MultipleChoiceField": "choice",
    "TypedMultipleChoiceField": "choice",
    "ModelChoiceField": "foreignkey",
    "ModelMultipleChoiceField": "manytomany",
}


def _form_extra_field_descriptor(
    *,
    model_admin: ModelAdmin,
    model: type[Model],
    name: str,
    form_field: Any,
    is_readonly: bool,
) -> dict[str, Any]:
    """Descriptor for a form field NOT backed by a model field (#606).

    ModelAdmin's custom Forms (via ``ModelAdmin.get_form()``) routinely
    declare extra fields — e.g. ``forms.EmailField()`` on a Profile
    create-form when the email lives on a related User — that Django's
    legacy admin renders correctly. The SPA's descriptor pipeline
    needs an equivalent: map the form-field class to one of the
    existing wire types so the client renders the right input widget
    instead of falling through to ``unsupported`` + ``readonly``
    (which was the v1.x behaviour and broke every "create via
    related field" workflow on the SPA).

    `Textarea` widget on a CharField is collapsed to the multi-line
    ``text`` type (mirrors ``_apply_widget_override`` for model fields).
    Choices on a ChoiceField come through as `{value, label}` entries.
    """
    cls_name = type(form_field).__name__
    wire_type = _FORM_FIELD_TYPE.get(cls_name, "string")
    # CharField + Textarea widget = multi-line text (same convention
    # as `_apply_widget_override` for model fields).
    if wire_type == "string" and isinstance(form_field.widget, Textarea):
        wire_type = "text"
    descriptor: dict[str, Any] = {
        "type": wire_type,
        "label": _field_label(model_admin, model, name),
        "required": bool(getattr(form_field, "required", False)),
        "readonly": is_readonly,
        "help_text": str(getattr(form_field, "help_text", "") or ""),
        # Initial value, if the form sets one — empty otherwise (the
        # add-form `obj=None` has nothing for this field on the model
        # side anyway). Coerced through the field's `prepare_value`
        # so a callable initial resolves to a plain wire value.
        "value": (
            form_field.prepare_value(
                getattr(form_field, "initial", None),
            )
            if hasattr(form_field, "prepare_value")
            else None
        ),
    }
    # ChoiceField-shaped fields expose `{value, label}` entries the
    # client renders as a <select>. Lazy translation proxies coerced
    # via str() so they resolve to the request locale. Lazy iterators
    # (e.g. `ModelChoiceIterator`) may raise on the first iteration if
    # the queryset isn't ready — `suppress` keeps that defensive: the
    # client renders an empty select rather than 500ing.
    raw_choices = getattr(form_field, "choices", None)
    if raw_choices and wire_type in {"choice", "foreignkey", "manytomany"}:
        with suppress(TypeError, ValueError):
            descriptor["choices"] = [{"value": v, "label": str(lbl)} for v, lbl in raw_choices]
    return descriptor


def _readonly_callable_descriptor(
    model_admin: ModelAdmin,
    model: type[Model],
    obj: Model,
    name: str,
) -> dict[str, Any]:
    """Descriptor for a readonly callable / method (no underlying field).

    ``ModelAdmin.get_fields`` may include method names or
    ``@admin.display`` callables; those have no model field, so they
    are surfaced as ``type=unsupported`` and ``readonly=True``.

    Resolution uses Django's own ``lookup_field(name, obj,
    model_admin)`` — the same helper the list view + the legacy admin
    use — so a method defined on the **ModelAdmin** (the common
    ``@admin.display def display_x(self, obj)`` pattern, called with
    ``obj``) resolves correctly, not just methods on the model. A naive
    ``getattr(obj, name)`` misses admin methods and returns ``None``
    (Issue #226). The defensive ``except`` keeps a raising method from
    500-ing the detail endpoint.
    """
    try:
        _f, _attr, value = lookup_field(name, obj, model_admin)
    except Exception:
        # Fallback: a plain attribute / method on the model instance.
        # The whole resolution is guarded because a readonly property
        # can *raise* (not merely be missing) — e.g. a model property
        # that assumes a saved instance and blows up on the unsaved
        # object behind the add-form. ``getattr(obj, name, None)`` only
        # swallows ``AttributeError``, so any other exception from the
        # property getter would otherwise propagate and 500 the endpoint
        # (Issue #275). Both catches are best-effort, kept broad on
        # purpose; logged so a misbehaving getter is observable.
        logger.warning("lookup_field failed for readonly field %r; trying getattr", name)
        try:
            value = getattr(obj, name, None)
            if callable(value):
                value = value()
        except Exception:
            logger.warning("resolving readonly field %r failed; using null", name, exc_info=True)
            value = None
    return {
        "type": "unsupported",
        "label": _field_label(model_admin, model, name),
        "required": False,
        "readonly": True,
        "help_text": "",
        "value": serialize_value(value),
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _serialize_file_value(value: Any) -> dict[str, Any] | None:
    """Serialize a ``FileField``/``ImageField`` value as ``{name, url, size}``.

    Returns ``None`` for empty fields. ``url`` defers to the
    consumer's storage backend (``value.url``) so signed-URL
    backends (S3, GCS, custom) work without package changes —
    we never construct a URL ourselves. ``size`` is best-effort:
    some backends don't expose it cheaply (a HEAD request to S3),
    so we swallow exceptions and return ``None`` for size when
    unavailable.
    """
    if not value:
        return None
    name = getattr(value, "name", "") or ""
    if not name:
        return None
    # Best-effort: ``url`` / ``size`` defer to the consumer's storage backend
    # and may raise (e.g. a remote HEAD failing); degrade to ``None`` for that
    # attribute rather than 500 the detail view. Kept broad on purpose; logged.
    try:
        url = value.url
    except Exception:
        logger.warning("reading file url failed for %r; using null", name, exc_info=True)
        url = None
    try:
        size = value.size
    except Exception:
        logger.warning("reading file size failed for %r; using null", name, exc_info=True)
        size = None
    return {"name": name, "url": url, "size": size}


def _field_label(model_admin: ModelAdmin, model: type[Model], name: str) -> str:
    """Human-readable label for a field (Django's own helper, with fallback)."""
    # Best-effort: ``label_for_field`` resolves a possibly consumer-defined
    # callable; fall back to the raw name rather than 500. Kept broad; logged.
    try:
        return str(label_for_field(name, model, model_admin))
    except Exception:
        logger.warning("label_for_field failed for %r; using raw name", name, exc_info=True)
        return name

"""Shared ModelAdmin **form-spec** resolver (Issue #59).

A single function — :func:`build_form_spec` — resolves the full
``(model, instance, request)`` form the way Django's admin change/add
view would, and serialises it to one JSON payload. Two consumers share
this resolver so they can never drift:

- the REST endpoint ``GET /api/v1/<app>/<model>/[<pk>|add]/form-spec/``
  (``views/form_spec.py``);
- the MCP ``admin.form_spec`` tool (``django-admin-mcp-api`` #70), which
  imports this module directly.

What the model serializer alone cannot see — and this resolver does —
is the **ModelAdmin layer**: request-aware ``get_form`` /
``get_fieldsets`` / ``get_readonly_fields`` overrides, ``formfield_overrides``
widgets, custom ``Form`` classes, and the admin's relation widgets
(autocomplete / raw-id / filter shuttle). Each field's resolved widget
is mapped to a **closed ``widget.kind`` enum** (every stdlib + admin
widget), with a ``custom`` fallback that carries the widget's dotted
class path + ``template_name`` so a consumer-side renderer can dispatch.

The per-field *value* serialisation deliberately reuses the detail
view's ``_descriptor_for`` builder, so a field's ``initial`` /
``choices`` / ``readonly`` are byte-for-byte identical to what the
detail and add-form endpoints already emit (one field vocabulary, no
second source of truth).

Wire contract: ``docs/api-contract.md`` §4.1.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Final

from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.forms import Widget
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch
from django.urls import reverse

from django_admin_rest_api.api.views.detail import _descriptor_for
from django_admin_rest_api.api.views.detail import _fieldsets_payload
from django_admin_rest_api.api.views.detail import _visible_field_names

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Closed widget.kind vocabulary                                               #
# --------------------------------------------------------------------------- #
# Maps a Django widget class *name* to the closed wire ``kind`` enum. Keyed
# by class name (not the class object) so the table needs no imports of the
# admin / postgres widget modules — a consumer that doesn't install
# ``django.contrib.postgres`` (or Pillow) still loads this module. Resolution
# in ``widget_kind`` walks the widget's MRO, so a stock subclass (e.g.
# ``AdminTextInputWidget(TextInput)``) resolves to its base's kind even if the
# subclass itself isn't listed. Anything with no recognised Django ancestor
# falls through to ``"custom"``.
_WIDGET_KIND_BY_NAME: Final[dict[str, str]] = {
    # --- plain inputs (django.forms.widgets) ---
    "TextInput": "text",
    "NumberInput": "number",
    "EmailInput": "email",
    "URLInput": "url",
    "PasswordInput": "password",
    "HiddenInput": "hidden",
    "MultipleHiddenInput": "hidden",
    "Textarea": "textarea",
    "DateInput": "date",
    "DateTimeInput": "datetime",
    "TimeInput": "time",
    "CheckboxInput": "checkbox",
    "Select": "select",
    "NullBooleanSelect": "select",
    "SelectMultiple": "select-multiple",
    "RadioSelect": "radio",
    "CheckboxSelectMultiple": "checkbox-multiple",
    "FileInput": "file",
    "ClearableFileInput": "file",
    "SplitDateTimeWidget": "split-datetime",
    "SplitHiddenDateTimeWidget": "hidden",
    "SelectDateWidget": "select-date",
    # --- admin widgets (django.contrib.admin.widgets) ---
    "AdminTextInputWidget": "text",
    "AdminEmailInputWidget": "email",
    "AdminURLFieldWidget": "url",
    "AdminIntegerFieldWidget": "number",
    "AdminBigIntegerFieldWidget": "number",
    "AdminUUIDInputWidget": "text",
    "AdminTextareaWidget": "textarea",
    "AdminDateWidget": "date",
    "AdminTimeWidget": "time",
    "AdminSplitDateTime": "split-datetime",
    "AdminRadioSelect": "radio",
    "AdminFileWidget": "file",
    "ForeignKeyRawIdWidget": "raw-id",
    "ManyToManyRawIdWidget": "raw-id",
    "FilteredSelectMultiple": "shuttle",
    "AutocompleteSelect": "autocomplete",
    "AutocompleteSelectMultiple": "autocomplete-multiple",
}

# The closed set the client may switch on. Kept as a module constant so a
# contract test (and the MCP parity test) can assert the enum hasn't drifted.
WIDGET_KINDS: Final[frozenset[str]] = frozenset(_WIDGET_KIND_BY_NAME.values()) | {"custom"}


def _unwrap_widget(widget: Widget) -> Widget:
    """Unwrap admin's ``RelatedFieldWidgetWrapper`` to the real inner widget.

    The admin wraps FK/M2M selects in a ``RelatedFieldWidgetWrapper`` that
    adds the green +/edit/view links. The wrapper carries the real control
    on ``.widget``; the wrapper class itself isn't a meaningful ``kind``.
    """
    inner: object = getattr(widget, "widget", None)
    if isinstance(inner, Widget) and inner is not widget:
        return inner
    return widget


def widget_kind(widget: Widget | None) -> str:
    """Resolve a Django widget to the closed wire ``kind`` enum.

    Resolution order:

    1. ``None`` → ``"custom"`` (no widget to classify; the safe fallback).
    2. The widget's own class name in the table.
    3. The first ancestor in its MRO that the table knows (so a stock
       subclass — ``AdminTextInputWidget(TextInput)``, a consumer's
       ``MyInput(TextInput)`` — resolves to its base's kind).
    4. ``"custom"`` — no recognised Django ancestor.
    """
    if widget is None:
        return "custom"
    widget = _unwrap_widget(widget)
    for klass in type(widget).__mro__:
        kind = _WIDGET_KIND_BY_NAME.get(klass.__name__)
        if kind is not None:
            return kind
    return "custom"


def _jsonable_attrs(attrs: Any) -> dict[str, Any]:
    """Return the widget's HTML attrs as a JSON-safe ``{str: scalar}`` dict.

    ``formfield_overrides`` and field config land their effect here
    (``{"rows": 10}`` for a forced ``Textarea``, ``{"maxlength": 255}``
    from a ``CharField``). Non-scalar attr values (rare) are coerced via
    ``str`` so the payload is always serialisable; ``None`` is dropped.
    """
    if not isinstance(attrs, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None:
            continue
        out[str(key)] = value if isinstance(value, str | int | float | bool) else str(value)
    return out


def widget_object(form_field: Any) -> dict[str, Any]:
    """Build the ``{kind, attrs[, widget_class, template_name]}`` widget block.

    ``widget_class`` (dotted Python path) + ``template_name`` are attached
    whenever the widget class lives **outside** ``django.*`` — i.e. it came
    from the consumer's app, a custom ``Form``, ``formfield_overrides``, or
    a third-party library. They are attached *regardless of ``kind``* so a
    consumer-registered renderer can dispatch even on a widget that
    subclasses a stock control (matching the #625 custom-widget protocol),
    while ``kind`` still carries the stock fallback so a client with no
    plugin can render *something*.
    """
    widget = getattr(form_field, "widget", None) if form_field is not None else None
    block: dict[str, Any] = {
        "kind": widget_kind(widget),
        "attrs": _jsonable_attrs(getattr(widget, "attrs", None)),
    }
    if widget is not None:
        inner = _unwrap_widget(widget)
        module = type(inner).__module__
        if not module.startswith("django."):
            block["widget_class"] = f"{module}.{type(inner).__name__}"
            template_name = getattr(inner, "template_name", None)
            if template_name:
                block["template_name"] = str(template_name)
    return block


# --------------------------------------------------------------------------- #
# Legacy-template escape hatch                                                 #
# --------------------------------------------------------------------------- #
# The final candidate Django's admin always appends to the change/add form
# template list (``ModelAdmin.render_change_form``). Its presence in a
# ``TemplateResponse.template_name`` means "this is the stock change form" —
# i.e. exactly what the JSON form-spec is able to reproduce.
_STANDARD_CHANGE_FORM_TEMPLATE: Final[str] = "admin/change_form.html"


def _renders_custom_template(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any,
    *,
    change: bool,
) -> bool:
    """Return ``True`` if the ModelAdmin's *overridden* add/change view
    renders something other than the stock admin change form.

    This catches the common pattern the ``change_form_template`` attribute
    check misses: a ``change_view`` (or ``add_view``) override that branches
    on ``request.GET`` and ``render(...)``s a hand-rolled template — e.g.
    ``def change_view(self, request, ...): if request.GET.get("run_custom"):
    return self.run_custom_view(...)``. There is no template *attribute* to
    inspect, only the response the view actually returns.

    Only probes when the view method is genuinely overridden — a stock
    ``ModelAdmin`` never pays this cost. The view is invoked with the real
    (GET) form-spec request, so a request-aware override resolves the same
    branch the SPA / legacy admin would.

    SECURITY NOTE (#70): because the override is *invoked* here on a GET, an
    overridden ``change_view`` / ``add_view`` MUST stay GET-idempotent — i.e.
    a GET must not mutate state. This is already Django's own contract for
    those views (a GET renders the form; mutation happens on POST), so a
    well-behaved override is unaffected. An override that writes on GET (an
    anti-pattern) would run that side effect on a form-spec read. See
    ``SECURITY.md`` ("Form-spec introspection probe"). A lazy
    ``TemplateResponse`` carrying
    ``admin/change_form.html`` is the stock form (→ render the JSON spec);
    anything else — a different template, a ``render()`` ``HttpResponse``, a
    redirect — means the SPA can't reproduce it, so fall back to the iframe.

    On any probe error the safe default is ``False`` (render the JSON spec),
    matching the conservative fall-through used elsewhere in this module.
    """
    base = ModelAdmin.change_view if change else ModelAdmin.add_view
    overridden = type(model_admin).change_view if change else type(model_admin).add_view
    if overridden is base:
        return False
    try:
        if change:
            response = model_admin.change_view(request, str(obj.pk))
        else:
            response = model_admin.add_view(request)
    except Exception:  # noqa: BLE001 — a view that errs on probe falls back to the JSON spec
        logger.warning(
            "form-spec legacy probe: %s.%s_view raised; rendering JSON spec instead",
            type(model_admin).__qualname__,
            "change" if change else "add",
            exc_info=True,
        )
        return False
    if isinstance(response, TemplateResponse):
        # ``template_name`` may be a single name, a ``Template`` instance, or
        # a list mixing both. Only the stock string list carries
        # ``admin/change_form.html``; a bare ``Template`` (compiled custom
        # template) is custom by definition.
        raw = response.template_name
        if isinstance(raw, str):
            names = [raw]
        elif isinstance(raw, list | tuple):
            names = [n for n in raw if isinstance(n, str)]
        else:
            names = []
        return _STANDARD_CHANGE_FORM_TEMPLATE not in names
    # A non-``TemplateResponse`` (a ``render()`` HttpResponse, a redirect, a
    # JsonResponse, …) is by definition not the stock change form.
    return True


def _legacy_renderer(
    model_admin: ModelAdmin,
    request: HttpRequest,
    app_label: str,
    model_name: str,
    obj: Any,
    *,
    change: bool,
) -> dict[str, Any] | None:
    """Return a ``legacy-iframe`` renderer payload, or ``None``.

    Two cases route to the iframe escape hatch, because in neither can the
    SPA faithfully rebuild the form from the JSON spec:

    1. The ModelAdmin sets ``change_form_template`` (change view) or
       ``add_form_template`` (add view) — a declared custom template.
    2. The ModelAdmin overrides ``change_view`` / ``add_view`` and that
       override renders a non-standard template for *this* request (e.g. a
       ``?run_custom=1`` branch that returns a custom dual-listbox page).

    Rather than silently drop the integrator's customisation (README #624),
    the spec tells the client to embed the legacy admin change/add page in an
    iframe for *that one view*. The rest of the SPA shell is untouched.

    Returns ``None`` (→ render the normal spec) when neither case applies, or
    when the legacy admin URL can't be reversed (the integrator may have
    unmounted ``django.contrib.admin`` entirely — in that case there's
    nothing to iframe, so fall through to the JSON spec).
    """
    template = getattr(model_admin, "change_form_template" if change else "add_form_template", None)
    if not template and not _renders_custom_template(model_admin, request, obj, change=change):
        return None
    site_name = getattr(getattr(model_admin, "admin_site", None), "name", "admin")
    view = "change" if change else "add"
    args = [obj.pk] if change and obj is not None else []
    try:
        legacy_url = reverse(f"{site_name}:{app_label}_{model_name}_{view}", args=args)
    except NoReverseMatch:
        logger.warning(
            "form-spec legacy-iframe fallback: cannot reverse %s admin URL for %s.%s; "
            "rendering JSON spec instead",
            view,
            app_label,
            model_name,
        )
        return None
    query = request.META.get("QUERY_STRING", "")
    if query:
        legacy_url = f"{legacy_url}?{query}"
    return {"renderer": "legacy-iframe", "legacy_url": legacy_url}


# --------------------------------------------------------------------------- #
# The resolver                                                                #
# --------------------------------------------------------------------------- #
def build_form_spec(
    model: type[Model],
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any | None,
    *,
    admin_site: Any,
) -> dict[str, Any]:
    """Resolve the full ModelAdmin form spec for ``(model, obj, request)``.

    ``obj is None`` → the add-view form (``get_form(request, obj=None,
    change=False)``); otherwise the change-view form for ``obj``
    (``get_form(request, obj=obj, change=True)``) — exactly how Django's
    ``_changeform_view`` builds each.

    Returns either:

    - the JSON form spec — ``{renderer: "form-spec", fieldsets, fields,
      variant}`` — where ``fields[name]`` carries ``{label, help_text,
      required, readonly, type, widget: {kind, attrs, …}, initial,
      choices, errors}``; or
    - the escape hatch ``{renderer: "legacy-iframe", legacy_url}`` when the
      admin overrides the change/add form template.

    No permission gating happens here — the caller (REST view / MCP tool)
    owns the staff + per-object ``has_*_permission`` gates. This function
    assumes access has already been granted.
    """
    change = obj is not None
    app_label = model._meta.app_label
    # ``model_name`` is non-None for any concrete registered model; coerce for
    # the type checker (the stub types it ``str | None``).
    model_name = str(model._meta.model_name)

    legacy = _legacy_renderer(model_admin, request, app_label, model_name, obj, change=change)
    if legacy is not None:
        return legacy

    visible_names = _visible_field_names(model_admin, request, obj)
    readonly = set(model_admin.get_readonly_fields(request, obj) or ())

    # Build the live form exactly how Django's add/change view does, so a
    # request-aware ``get_form`` override is honoured (the whole point of #59).
    if change:
        form = model_admin.get_form(request, obj=obj, change=True)(instance=obj)
        value_obj: Any = obj
    else:
        form = model_admin.get_form(request, obj=None, change=False)()
        # Unsaved instance so the shared descriptor builder has field
        # defaults to read (FK → None, M2M → []).
        value_obj = model()

    fields: dict[str, dict[str, Any]] = {}
    for name in visible_names:
        base = _descriptor_for(
            model=model,
            model_admin=model_admin,
            obj=value_obj,
            name=name,
            form=form,
            is_readonly=name in readonly,
            admin_site=admin_site,
            request=request,
        )
        form_field = form.fields.get(name)
        entry: dict[str, Any] = {
            "label": base["label"],
            "help_text": base["help_text"],
            "required": base["required"],
            "readonly": base["readonly"],
            # ``type`` (the field-level wire vocabulary) is kept alongside the
            # widget block: ``kind`` is *how* to render, ``type`` is *what the
            # value is*. Clients may use either; both come from one resolution.
            "type": base["type"],
            "widget": widget_object(form_field),
            # ``initial`` reuses the detail/add value serialisation verbatim
            # (FK → {id,label}, M2M → [..], PasswordInput-redacted → null).
            "initial": base["value"],
            "errors": [],
        }
        # Carry through the optional descriptor extras when present so the
        # client has the same metadata the detail endpoint exposes.
        for extra in ("choices", "to", "max_length", "decimal_places"):
            if extra in base:
                entry[extra] = base[extra]
        fields[name] = entry

    return {
        "renderer": "form-spec",
        "fieldsets": _fieldsets_payload(model_admin, request, obj, visible_names),
        "fields": fields,
        # ``variant``: the resolved Form class's dotted path. A request-aware
        # ``get_form`` that returns different Form classes for ``?variant=…``
        # or per-user surfaces a different value here, letting the client
        # detect (and cache-key on) the switch. Stable for a fixed request.
        "variant": f"{type(form).__module__}.{type(form).__qualname__}",
    }

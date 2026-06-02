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
import re
from html.parser import HTMLParser
from typing import Any
from typing import Final

from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.forms import Widget
from django.http import HttpRequest
from django.http import HttpResponse
from django.http.response import HttpResponseRedirectBase
from django.middleware.csrf import get_token
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch
from django.urls import reverse

from django_admin_rest_api import conf
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
    redirect — means the SPA can't reproduce it from the JSON spec, so we
    render it server-side and return an ``html-fragment`` (#75).

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


# --------------------------------------------------------------------------- #
# Admin-chrome stripping                                                       #
# --------------------------------------------------------------------------- #
# Django's ``admin/base.html`` wraps ``{% block content %}`` (plus the default
# ``content_title`` / ``content_subtitle`` h1/h2) in ``<div id="content">``. We
# extract that div's inner HTML — everything else (``<html>``, ``<head>``,
# ``<header id="header">``, ``#nav-sidebar``, ``#footer``) is chrome the SPA
# already renders, so it's dropped. ``<script>`` / ``<style>`` the integrator's
# template emits *inside* the content block live within this div, so they
# survive verbatim (they're part of the form contract — #75).
_CONTENT_DIV_ID: Final[str] = "content"


class _ContentBlockExtractor(HTMLParser):
    """Capture the inner HTML of ``<div id="content">`` from a rendered page.

    Stdlib-only (no bs4 / lxml dependency). Depth-counts nested ``<div>``s so
    the matching close tag is found even when the content block contains its
    own divs. ``convert_charrefs=False`` keeps entity references byte-faithful
    so an integrator's ``&amp;`` / ``&lt;`` in the fragment is preserved.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._capturing = False
        self._depth = 0
        self._parts: list[str] = []
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if not self._capturing:
            if tag == "div" and dict(attrs).get("id") == _CONTENT_DIV_ID:
                self._capturing = True
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        self._parts.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Void / self-closing tags (``<input .../>``, ``<img .../>``).
        if self._capturing and not self.done:
            self._parts.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if not self._capturing or self.done:
            return
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self.done = True
                return
        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._capturing and not self.done:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capturing and not self.done:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capturing and not self.done:
            self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self._capturing and not self.done:
            self._parts.append(f"<!--{data}-->")

    @property
    def html(self) -> str:
        return "".join(self._parts).strip()


def strip_admin_chrome(rendered: str) -> str:
    """Return only the inside of ``<div id="content">`` from a rendered admin
    page, with surrounding chrome removed and the integrator's inline
    ``<script>`` / ``<style>`` preserved verbatim.

    Falls back to the full body (minus a leading doctype/html) if the content
    div can't be located — better to over-include than to drop the form.
    """
    parser = _ContentBlockExtractor()
    parser.feed(rendered)
    parser.close()
    fragment = parser.html
    if fragment:
        return fragment
    # Defensive fallback: no ``#content`` div found (a wildly non-stock base).
    # Strip the doctype + outer html/head so the SPA still gets a body-ish
    # fragment rather than a full document.
    logger.warning("html-fragment: no <div id=content> found; returning body fallback")
    body = re.search(r"<body[^>]*>(.*)</body>", rendered, re.IGNORECASE | re.DOTALL)
    return (body.group(1) if body else rendered).strip()


# --------------------------------------------------------------------------- #
# Django messages capture                                                      #
# --------------------------------------------------------------------------- #
def capture_messages(request: HttpRequest) -> list[dict[str, str]]:
    """Drain the request's Django ``messages`` into ``[{level, text}]``.

    The html-fragment round-trip has no browser session redirect to flush the
    message storage, so we read it directly off the synthetic/SPA request and
    surface it in the JSON payload for the SPA to toast (#75). ``get_messages``
    marks the storage used, so each message is returned exactly once.
    """
    from django.contrib.messages import get_messages

    out: list[dict[str, str]] = []
    try:
        for message in get_messages(request):
            out.append({"level": message.level_tag or "info", "text": str(message)})
    except Exception:  # noqa: BLE001 — messages framework not configured / no storage
        logger.warning("html-fragment: could not read messages storage", exc_info=True)
    return out


# --------------------------------------------------------------------------- #
# Redirect → SPA URL mapping                                                   #
# --------------------------------------------------------------------------- #
_LEGACY_ADMIN_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^/[^/]+/")


def map_redirect_to_spa(location: str) -> str:
    """Rewrite a legacy admin redirect target onto the configured SPA prefix.

    A legacy change_view returns ``HttpResponseRedirect`` to e.g.
    ``/admin/jobs/job/1/change/``; the SPA navigates within its own shell, so
    we swap the leading ``/<admin-mount>/`` segment for ``conf.SPA_URL_PREFIX``
    (default ``/admin2/``) → ``/admin2/jobs/job/1/change/`` (#75). Absolute
    (``http://``) or non-admin targets pass through unchanged.
    """
    spa_prefix = conf.SPA_URL_PREFIX
    if not spa_prefix.endswith("/"):
        spa_prefix = f"{spa_prefix}/"
    if not location.startswith("/"):
        return location
    # Replace only the first path segment (the admin mount, e.g. ``/admin/``).
    return _LEGACY_ADMIN_PREFIX_RE.sub(spa_prefix, location, count=1)


# --------------------------------------------------------------------------- #
# html-fragment renderer                                                       #
# --------------------------------------------------------------------------- #
def _resolve_legacy_response(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any,
    *,
    change: bool,
) -> HttpResponse:
    """Invoke the ModelAdmin's real change/add view with the SPA caller's
    request (auth + querystring + POST data already on it) and return the
    response. The caller decides whether to render it as an html-fragment.
    """
    if change:
        return model_admin.change_view(request, str(obj.pk))
    return model_admin.add_view(request)


def _render_response_html(response: HttpResponse) -> str:
    """Return the rendered HTML body of a (possibly lazy) admin response."""
    if isinstance(response, TemplateResponse) and not response.is_rendered:
        response.render()
    return response.content.decode(response.charset or "utf-8")


def _submit_url(model_admin: ModelAdmin, request: HttpRequest, obj: Any, *, change: bool) -> str:
    """The legacy admin change/add URL (+ original querystring) the SPA POSTs
    back to. This is the ``action`` target the html-fragment's form wires to.
    """
    site_name = getattr(getattr(model_admin, "admin_site", None), "name", "admin")
    app_label = model_admin.model._meta.app_label
    model_name = str(model_admin.model._meta.model_name)
    view = "change" if change else "add"
    args = [obj.pk] if change and obj is not None else []
    url = reverse(f"{site_name}:{app_label}_{model_name}_{view}", args=args)
    query = request.META.get("QUERY_STRING", "")
    return f"{url}?{query}" if query else url


def _html_fragment_payload(
    model_admin: ModelAdmin,
    request: HttpRequest,
    response: HttpResponse,
    obj: Any,
    *,
    change: bool,
) -> dict[str, Any]:
    """Build the ``html-fragment`` payload from a rendered admin response."""
    html = strip_admin_chrome(_render_response_html(response))
    return {
        "renderer": "html-fragment",
        "html": html,
        "csrf_token": get_token(request),
        "submit_url": _submit_url(model_admin, request, obj, change=change),
        "method": "POST",
        "messages": capture_messages(request),
    }


def _custom_renderer(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any,
    *,
    change: bool,
) -> dict[str, Any] | None:
    """Return an ``html-fragment`` payload when the ModelAdmin renders a custom
    template, or ``None`` when the stock JSON form-spec applies (#75).

    Two cases route to server-side rendering, because in neither can the SPA
    faithfully rebuild the form from the JSON spec:

    1. The ModelAdmin sets ``change_form_template`` (change view) or
       ``add_form_template`` (add view) — a declared custom template.
    2. The ModelAdmin overrides ``change_view`` / ``add_view`` and that
       override renders a non-standard template for *this* request (e.g. a
       ``?run_custom=1`` branch returning a hand-rolled dual-listbox page).

    Rather than drop the integrator's customisation, we invoke the real view
    server-side (carrying the SPA caller's auth + querystring), strip the admin
    chrome, and hand the SPA the content-block HTML to inject in-place — no
    iframe, no X-Frame-Options / SameSite failure mode. The rest of the SPA
    shell is untouched.

    Returns ``None`` (→ render the JSON spec) when neither case applies, or
    when reversing the legacy admin URL fails (the integrator may have
    unmounted ``django.contrib.admin`` — nothing to render server-side).
    """
    template = getattr(model_admin, "change_form_template" if change else "add_form_template", None)
    if not template and not _renders_custom_template(model_admin, request, obj, change=change):
        return None
    try:
        response = _resolve_legacy_response(model_admin, request, obj, change=change)
        payload = _html_fragment_payload(model_admin, request, response, obj, change=change)
    except NoReverseMatch:
        logger.warning(
            "html-fragment: cannot reverse %s admin URL for %s; rendering JSON spec instead",
            "change" if change else "add",
            type(model_admin).__qualname__,
        )
        return None
    except Exception:  # noqa: BLE001 — a view that errs falls back to the JSON spec
        logger.warning(
            "html-fragment: %s.%s_view raised while rendering; rendering JSON spec instead",
            type(model_admin).__qualname__,
            "change" if change else "add",
            exc_info=True,
        )
        return None
    return payload


def render_change_post(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any,
) -> dict[str, Any]:
    """Re-run the legacy change_view server-side for a POST round-trip (#75).

    The SPA POSTs the html-fragment's form back to
    ``/api/v1/<app>/<model>/<pk>/change/?<qs>``; this forwards the POST data +
    querystring (already on ``request``) into the real ``change_view`` and maps
    its response to one of two payloads:

    - ``{"renderer": "redirect", "to": "<SPA url>", "messages": […]}`` when the
      view returns an ``HttpResponseRedirect`` (success path), the legacy
      target rewritten onto the SPA prefix; or
    - another ``html-fragment`` when the view re-renders (e.g. validation
      errors), so the SPA can re-display the form.

    Django messages set by the view are captured into the payload either way.

    A redirect *back to the same change page* is the legacy
    Post/Redirect/Get-to-self idiom a view uses to re-display the form with a
    flash error (the fixture's empty-selection branch does
    ``redirect(request.get_full_path())``). We don't bounce that to the SPA —
    we follow it server-side, re-render the form, and return an html-fragment
    carrying the captured messages, so the SPA re-displays the form with the
    error toast. A redirect *elsewhere* (the success path) becomes a redirect
    payload mapped onto the SPA prefix.
    """
    response = _resolve_legacy_response(model_admin, request, obj, change=True)
    # Drain messages off the POST request now — a self-redirect re-render uses
    # a fresh synthetic GET whose storage wouldn't carry them.
    captured = capture_messages(request)
    if isinstance(response, HttpResponseRedirectBase):
        location = response["Location"]
        if _is_self_redirect(location, request):
            return _rerender_after_post(model_admin, request, obj, messages=captured)
        return {
            "renderer": "redirect",
            "to": map_redirect_to_spa(location),
            "messages": captured,
        }
    payload = _html_fragment_payload(model_admin, request, response, obj, change=True)
    payload["messages"] = captured
    return payload


def _is_self_redirect(location: str, request: HttpRequest) -> bool:
    """True if ``location`` points back to the current request's own path —
    the PRG-to-self idiom a view uses to re-display the form with a flash."""
    from urllib.parse import urlparse

    return urlparse(location).path == request.path


def _rerender_after_post(
    model_admin: ModelAdmin,
    request: HttpRequest,
    obj: Any,
    *,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Re-render the change form via a synthetic GET (carrying the POST
    request's auth + querystring) and return an html-fragment whose
    ``messages`` are the ones the POST set (passed in ``messages``)."""
    get_request = _synthetic_get(request)
    response = _resolve_legacy_response(model_admin, get_request, obj, change=True)
    payload = _html_fragment_payload(model_admin, get_request, response, obj, change=True)
    payload["messages"] = messages
    return payload


def _synthetic_get(request: HttpRequest) -> HttpRequest:
    """Build a GET request mirroring ``request``'s auth/session/querystring.

    Reuses the SPA caller's identity (``user``/``session``) and original
    querystring so a request-aware view resolves the same ``?run_custom=1``
    branch. No security-bypass flags are copied — the synthetic request is a
    plain GET gated by the same permission checks the POST already passed.
    """
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    factory = RequestFactory()
    query = request.META.get("QUERY_STRING", "")
    path = request.path if not query else f"{request.path}?{query}"
    get_request = factory.get(path)
    get_request.user = request.user
    if hasattr(request, "session"):
        get_request.session = request.session
    get_request.COOKIES = request.COOKIES
    # Fresh message storage so the re-render's template can read messages
    # without error (the captured POST messages are attached to the payload).
    get_request._messages = FallbackStorage(get_request)  # type: ignore[attr-defined]
    return get_request


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
    - the server-rendered ``{renderer: "html-fragment", html, csrf_token,
      submit_url, method, messages}`` when the admin renders a custom
      template the JSON spec can't reproduce (#75).

    No permission gating happens here — the caller (REST view / MCP tool)
    owns the staff + per-object ``has_*_permission`` gates. This function
    assumes access has already been granted.
    """
    change = obj is not None

    custom = _custom_renderer(model_admin, request, obj, change=change)
    if custom is not None:
        return custom

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

    payload: dict[str, Any] = {
        "renderer": "form-spec",
        "fieldsets": _fieldsets_payload(model_admin, request, obj, visible_names),
        "fields": fields,
        # ``variant``: the resolved Form class's dotted path. A request-aware
        # ``get_form`` that returns different Form classes for ``?variant=…``
        # or per-user surfaces a different value here, letting the client
        # detect (and cache-key on) the switch. Stable for a fixed request.
        "variant": f"{type(form).__module__}.{type(form).__qualname__}",
    }
    # prepopulated_fields (#72): only meaningful on the ADD form — Django
    # slugifies the target from its sources as the user types a *new* object.
    # The add-form schema endpoint (`/add/`) already emits this; surface it on
    # the add form-spec too so a client that renders from the form-spec gets
    # the same slugify-on-keystroke signal.
    if not change:
        payload["prepopulated_fields"] = prepopulated_fields_payload(
            model_admin, request, list(visible_names), readonly
        )
    return payload


def prepopulated_fields_payload(
    model_admin: ModelAdmin,
    request: HttpRequest,
    visible_names: list[str],
    readonly: set[str],
) -> dict[str, list[str]]:
    """Build the ``{target: [sources]}`` prepopulated-fields block (#72 / #245).

    Returns ``ModelAdmin.get_prepopulated_fields(request, None)`` restricted to
    rendered, non-readonly targets with at least one rendered source. A
    misbehaving override degrades to ``{}`` rather than 500 the form. Shared by
    the add form-spec and the add-form schema endpoint so both agree.
    """
    try:
        raw = model_admin.get_prepopulated_fields(request, None) or {}
    except Exception:  # pragma: no cover — admin author error
        logger.warning("get_prepopulated_fields failed; none surfaced", exc_info=True)
        return {}
    visible = set(visible_names)
    out: dict[str, list[str]] = {}
    for target, sources in raw.items():
        if target not in visible or target in readonly:
            continue
        kept = [s for s in sources if s in visible]
        if kept:
            out[target] = kept
    return out

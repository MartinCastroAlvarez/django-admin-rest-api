"""Tests for the ModelAdmin form-spec endpoint + shared resolver (#59).

``GET /api/v1/<app>/<model>/[<pk>|add]/form-spec/`` resolves the live
ModelAdmin form (request-aware ``get_form`` / ``get_fieldsets`` /
``get_readonly_fields``) into one JSON payload with a closed
``widget.kind`` enum. Both the SPA (django-admin-react #659) and the MCP
tool (django-admin-mcp-api #70) consume the same resolver.

Covers the mandatory permission matrix plus: payload shape, widget-kind
mapping, request-aware ``get_form`` / ``get_fieldsets`` /
``get_readonly_fields``, ``formfield_overrides`` → ``widget.attrs``,
custom widgets → ``kind: "custom"`` + ``widget_class``, and the
``legacy-iframe`` escape hatch for ``change_form_template`` overrides.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import Client

from django_admin_rest_api.api import form_spec as fs
from tests.helpers import admin_override

ADD_URL = "/admin-api/api/v1/auth/group/add/form-spec/"


@contextmanager
def admin_attr(model_cls, **attrs):
    """Temporarily set plain (non-callable) attributes on a ModelAdmin.

    ``admin_override`` binds its values as methods, which is wrong for a
    plain string attribute like ``change_form_template``.
    """
    model_admin = admin.site._registry[model_cls]
    originals = {name: getattr(model_admin, name, None) for name in attrs}
    try:
        for name, value in attrs.items():
            setattr(model_admin, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(model_admin, name, value)


def _change_url(pk: int) -> str:
    return f"/admin-api/api/v1/auth/group/{pk}/form-spec/"


# --------------------------------------------------------------------------- #
# Permission matrix                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_anonymous_redirected_or_forbidden(anon_client: Client) -> None:
    assert anon_client.get(ADD_URL).status_code in (302, 403)


@pytest.mark.django_db
def test_non_staff_forbidden(user_client: Client) -> None:
    assert user_client.get(ADD_URL).status_code == 403


@pytest.mark.django_db
def test_staff_without_add_permission_forbidden_on_add(superuser_client: Client) -> None:
    """The add route is gated on has_add_permission, not view."""
    with admin_override(Group, has_add_permission=lambda self, request: False):
        assert superuser_client.get(ADD_URL).status_code == 403


@pytest.mark.django_db
def test_change_route_gated_on_per_object_view_permission(superuser_client: Client) -> None:
    """Model-level view allowed (obj=None → True) but this row denied
    (obj set → False) → 403, mirroring the detail endpoint's per-object
    gate. (A model-level denial returns 404 via resolve_model instead.)"""
    g = Group.objects.create(name="ops")
    with admin_override(Group, has_view_permission=lambda self, request, obj=None: obj is None):
        assert superuser_client.get(_change_url(g.pk)).status_code == 403


@pytest.mark.django_db
def test_unregistered_model_not_found(superuser_client: Client) -> None:
    assert (
        superuser_client.get("/admin-api/api/v1/auth/nonexistent/add/form-spec/").status_code == 404
    )


@pytest.mark.django_db
def test_unknown_pk_not_found(superuser_client: Client) -> None:
    assert superuser_client.get(_change_url(99999999)).status_code == 404


# --------------------------------------------------------------------------- #
# Payload shape                                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_add_payload_shape(superuser_client: Client) -> None:
    body = superuser_client.get(ADD_URL).json()
    assert body["renderer"] == "form-spec"
    assert "fieldsets" in body
    assert "fields" in body
    assert "variant" in body
    name = body["fields"]["name"]
    # Issue's documented per-field keys.
    for key in ("label", "help_text", "required", "readonly", "widget", "initial", "errors"):
        assert key in name, key
    assert name["readonly"] is False
    assert name["errors"] == []
    # The closed widget block.
    assert set(name["widget"]) >= {"kind", "attrs"}
    assert name["widget"]["kind"] in fs.WIDGET_KINDS


@pytest.mark.django_db
def test_change_payload_carries_initial_value(superuser_client: Client) -> None:
    g = Group.objects.create(name="editors")
    body = superuser_client.get(_change_url(g.pk)).json()
    assert body["renderer"] == "form-spec"
    assert body["fields"]["name"]["initial"] == "editors"
    # ``Cache-Control: no-store`` on the permission-gated payload.
    assert superuser_client.get(_change_url(g.pk))["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_no_store_on_add(superuser_client: Client) -> None:
    assert superuser_client.get(ADD_URL)["Cache-Control"] == "no-store"


# --------------------------------------------------------------------------- #
# Widget-kind mapping (resolver unit tests — no DB)                            #
# --------------------------------------------------------------------------- #
def test_widget_kind_stdlib() -> None:
    assert fs.widget_kind(forms.TextInput()) == "text"
    assert fs.widget_kind(forms.Textarea()) == "textarea"
    assert fs.widget_kind(forms.Select()) == "select"
    assert fs.widget_kind(forms.SelectMultiple()) == "select-multiple"
    assert fs.widget_kind(forms.CheckboxInput()) == "checkbox"
    assert fs.widget_kind(forms.RadioSelect()) == "radio"
    assert fs.widget_kind(forms.DateInput()) == "date"
    assert fs.widget_kind(forms.PasswordInput()) == "password"
    assert fs.widget_kind(forms.HiddenInput()) == "hidden"


def test_widget_kind_admin_widgets() -> None:
    from django.contrib.admin.widgets import AdminDateWidget
    from django.contrib.admin.widgets import AutocompleteSelect
    from django.contrib.admin.widgets import FilteredSelectMultiple
    from django.contrib.admin.widgets import ManyToManyRawIdWidget

    assert fs.widget_kind(AdminDateWidget()) == "date"
    assert fs.widget_kind(FilteredSelectMultiple("perms", is_stacked=False)) == "shuttle"
    # AutocompleteSelect / raw-id need a rel + admin_site; class-name mapping
    # is what matters, so assert via the table directly.
    assert fs._WIDGET_KIND_BY_NAME["AutocompleteSelect"] == "autocomplete"
    assert fs._WIDGET_KIND_BY_NAME["ManyToManyRawIdWidget"] == "raw-id"
    assert AutocompleteSelect.__name__ in fs._WIDGET_KIND_BY_NAME
    assert ManyToManyRawIdWidget.__name__ in fs._WIDGET_KIND_BY_NAME


def test_widget_kind_stock_subclass_resolves_to_base() -> None:
    class MyInput(forms.TextInput):
        pass

    assert fs.widget_kind(MyInput()) == "text"


def test_widget_kind_truly_custom_is_custom() -> None:
    class Totally(forms.Widget):
        pass

    assert fs.widget_kind(Totally()) == "custom"
    assert fs.widget_kind(None) == "custom"


def test_widget_object_attaches_widget_class_for_non_django() -> None:
    class MarkdownWidget(forms.Textarea):
        template_name = "mypkg/markdown.html"

    field = forms.CharField(widget=MarkdownWidget())
    block = fs.widget_object(field)
    # Stock fallback kind (Textarea base) keeps a sane render...
    assert block["kind"] == "textarea"
    # ...and the dotted path is surfaced for plugin dispatch (#625 parity).
    assert block["widget_class"].endswith(".MarkdownWidget")
    assert block["template_name"] == "mypkg/markdown.html"


def test_widget_object_no_widget_class_for_stock() -> None:
    field = forms.CharField(widget=forms.TextInput())
    block = fs.widget_object(field)
    assert block["kind"] == "text"
    assert "widget_class" not in block


# --------------------------------------------------------------------------- #
# formfield_overrides → widget.attrs                                          #
# --------------------------------------------------------------------------- #
def test_widget_attrs_reflects_formfield_overrides() -> None:
    field = forms.CharField(widget=forms.Textarea(attrs={"rows": 10, "data-x": None}))
    block = fs.widget_object(field)
    assert block["kind"] == "textarea"
    assert block["attrs"]["rows"] == 10
    # ``None`` attrs are dropped (always JSON-serialisable).
    assert "data-x" not in block["attrs"]


# --------------------------------------------------------------------------- #
# Request-aware get_form / get_fieldsets / get_readonly_fields                 #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_request_aware_get_form_switches_variant(superuser_client: Client) -> None:
    """A get_form that branches on request.GET surfaces a different
    ``variant`` — the whole point of #59."""

    class FormA(forms.ModelForm):
        class Meta:
            model = Group
            fields = ("name",)

    class FormB(forms.ModelForm):
        class Meta:
            model = Group
            fields = ("name",)

    def branching_get_form(self, request, obj=None, change=False, **kwargs):  # noqa: ANN001
        return FormB if request.GET.get("variant") == "b" else FormA

    with admin_override(Group, get_form=branching_get_form):
        variant_a = superuser_client.get(ADD_URL).json()["variant"]
        variant_b = superuser_client.get(f"{ADD_URL}?variant=b").json()["variant"]

    assert variant_a.endswith(".FormA")
    assert variant_b.endswith(".FormB")
    assert variant_a != variant_b


@pytest.mark.django_db
def test_request_aware_get_readonly_fields_honoured(superuser_client: Client) -> None:
    g = Group.objects.create(name="locked")

    def ro(self, request, obj=None):  # noqa: ANN001
        return ("name",) if obj is not None else ()

    with admin_override(Group, get_readonly_fields=ro):
        body = superuser_client.get(_change_url(g.pk)).json()
    assert body["fields"]["name"]["readonly"] is True


@pytest.mark.django_db
def test_get_fieldsets_honoured(superuser_client: Client) -> None:
    def fieldsets(self, request, obj=None):  # noqa: ANN001
        return (("Identity", {"fields": ("name",), "classes": ("collapse",)}),)

    with admin_override(Group, get_fieldsets=fieldsets):
        body = superuser_client.get(ADD_URL).json()
    fset = body["fieldsets"]
    assert fset[0]["title"] == "Identity"
    assert "collapse" in fset[0]["classes"]
    assert "name" in fset[0]["fields"]


# --------------------------------------------------------------------------- #
# Custom widget via formfield_overrides surfaces on the endpoint               #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_custom_form_widget_surfaces_widget_class(superuser_client: Client) -> None:
    class MarkdownWidget(forms.Textarea):
        template_name = "mypkg/markdown.html"

    class GroupForm(forms.ModelForm):
        class Meta:
            model = Group
            fields = ("name",)
            widgets = {"name": MarkdownWidget()}

    def get_form(self, request, obj=None, change=False, **kwargs):  # noqa: ANN001
        return GroupForm

    with admin_override(Group, get_form=get_form):
        body = superuser_client.get(ADD_URL).json()
    widget = body["fields"]["name"]["widget"]
    assert widget["kind"] == "textarea"
    assert widget["widget_class"].endswith(".MarkdownWidget")


# --------------------------------------------------------------------------- #
# legacy-iframe escape hatch                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_change_form_template_triggers_legacy_iframe(superuser_client: Client) -> None:
    """A ModelAdmin that overrides change_form_template can't be rendered
    from JSON — the spec returns a legacy-iframe pointer instead (#624)."""
    g = Group.objects.create(name="legacy")
    with admin_attr(Group, change_form_template="admin/custom_change.html"):
        body = superuser_client.get(_change_url(g.pk)).json()
    assert body["renderer"] == "legacy-iframe"
    assert "legacy_url" in body
    assert str(g.pk) in body["legacy_url"]


@pytest.mark.django_db
def test_add_form_template_triggers_legacy_iframe(superuser_client: Client) -> None:
    with admin_attr(Group, add_form_template="admin/custom_add.html"):
        body = superuser_client.get(ADD_URL).json()
    assert body["renderer"] == "legacy-iframe"
    assert body["legacy_url"].endswith("/add/")


# --------------------------------------------------------------------------- #
# Custom request-driven change_view fixture (Job) — the cross-repo contract.   #
#                                                                              #
# JobAdmin sets NO change_form_template; it overrides change_view to branch    #
# on ?run_custom=1 and render a hand-rolled template. The resolver must probe  #
# the view to tell the two paths apart (#59 / #70 / react #659).               #
# --------------------------------------------------------------------------- #
def _job_change_url(pk: int) -> str:
    return f"/admin-api/api/v1/jobs/job/{pk}/form-spec/"


@pytest.mark.django_db
def test_job_path_a_is_stock_form_spec_with_textarea_metadata(superuser_client: Client) -> None:
    """Path A — no ?run_custom: the stock change form is fully describable,
    and ``formfield_for_dbfield`` surfaces the large-textarea on ``metadata``."""
    from tests.test_project.jobs.models import Job

    job = Job.objects.create(name="nightly", metadata={"k": "v"}, status="idle")
    body = superuser_client.get(_job_change_url(job.pk)).json()

    assert body["renderer"] == "form-spec"
    meta = body["fields"]["metadata"]["widget"]
    assert meta["kind"] == "textarea"
    assert meta["attrs"]["class"] == "vLargeTextField"
    # The other fields render with their default widgets.
    assert body["fields"]["name"]["widget"]["kind"] == "text"
    assert body["fields"]["status"]["widget"]["kind"] == "text"


@pytest.mark.django_db
def test_job_path_b_run_custom_triggers_legacy_iframe(superuser_client: Client) -> None:
    """Path B — ?run_custom=1: change_view returns a custom render(), which
    the JSON spec can't reproduce, so the resolver emits the legacy iframe
    pointer with the query string preserved."""
    from tests.test_project.jobs.models import Job

    job = Job.objects.create(name="nightly", status="idle")
    body = superuser_client.get(f"{_job_change_url(job.pk)}?run_custom=1").json()

    assert body["renderer"] == "legacy-iframe"
    assert body["legacy_url"] == f"/admin/jobs/job/{job.pk}/change/?run_custom=1"


@pytest.mark.django_db
def test_job_add_view_is_stock_form_spec(superuser_client: Client) -> None:
    """The add view is not overridden, so the add route stays a JSON spec
    (the probe only fires on the overridden change_view)."""
    body = superuser_client.get("/admin-api/api/v1/jobs/job/add/form-spec/").json()
    assert body["renderer"] == "form-spec"
    assert body["fields"]["metadata"]["widget"]["kind"] == "textarea"

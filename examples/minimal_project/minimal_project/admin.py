"""Example ModelAdmin with one batch and one detail action.

Both actions follow Django's standard ``@admin.action`` decorator —
there is no package-specific API. The package classifies them by
signature:

- ``reprocess_batch(self, request, queryset)`` → ``target=batch``
  (the SPA renders this on the changelist with multi-select).
- ``reprocess_one(self, request, obj_id: str)`` → ``target=detail``
  (the SPA renders this on the single-object detail page).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib import messages
from django.db import models
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import path
from django.urls import reverse

from minimal_project.models import Invoice
from minimal_project.models import LineItem
from minimal_project.models import Note


@admin.action(description="Mark archived")
def mark_archived(modeladmin, request, queryset: QuerySet) -> None:  # noqa: ARG001
    """Batch shape: takes a queryset. Renders on the changelist."""
    queryset.update(archived=True)


@admin.action(description="Bump priority")
def bump_priority(modeladmin, request, obj_id: str) -> None:  # noqa: ARG001
    """Detail shape: takes a single object id. Renders on the detail page."""
    Note.objects.filter(pk=obj_id).update(priority=models.F("priority") + 1)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "priority", "archived", "created_at")
    list_filter = ("archived",)
    search_fields = ("title", "body")
    actions = [mark_archived, bump_priority]


class LineItemInline(admin.TabularInline):
    """Editable inline: operators add / edit / delete child rows in the
    same form as the parent ``Invoice``. The SPA renders these as an
    inline editor and writes them back in the parent ``PATCH`` via the
    ``"inlines"`` block — nothing package-specific, just a stock
    ``TabularInline`` with editable fields."""

    model = LineItem
    extra = 0
    fields = ("sku", "quantity", "unit_price")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Demonstrates all three "advanced admin" features over plain
    ``ModelAdmin`` hooks — no package-specific API:

    1. **Editable inline** — ``LineItemInline`` (add / edit / delete rows).
    2. **Custom change-form view** — ``?recalc=1`` branch in ``change_view``
       renders a non-stock template, which the form-spec endpoint serves as
       an ``html-fragment`` the SPA injects in place (no iframe).
    3. **``get_urls()`` custom views** — an object-level "Download invoice
       (CSV)" and a changelist-level "Export all invoices", surfaced as
       ``custom_views`` link descriptors the SPA links out to.
    """

    list_display = ("number", "customer", "created_at")
    search_fields = ("number", "customer")
    inlines = [LineItemInline]

    # --- Feature 2: custom change-form view (Path B → html-fragment). ---
    def change_view(self, request, object_id, form_url="", extra_context=None):  # noqa: ANN001
        if request.GET.get("recalc") == "1":
            return self.recalc_view(request, object_id)
        return super().change_view(request, object_id, form_url, extra_context)

    def recalc_view(self, request, object_id):  # noqa: ANN001
        invoice = self.get_object(request, object_id)
        if request.method == "POST":
            messages.success(request, f"Recalculated totals for {invoice.number}.")
            return redirect("admin:minimal_project_invoice_change", object_id)
        total = sum(i.quantity * i.unit_price for i in invoice.items.all())
        context = dict(
            self.admin_site.each_context(request),
            title=f"Recalculate {invoice.number}",
            opts=self.model._meta,
            invoice=invoice,
            total=total,
            cancel_url=reverse("admin:minimal_project_invoice_change", args=[invoice.pk]),
        )
        return render(request, "admin/minimal_project/invoice/recalc.html", context)

    # --- Feature 3: get_urls() custom views (download / export). ---
    def get_urls(self):  # noqa: ANN201
        app, model = self.opts.app_label, self.opts.model_name
        custom = [
            path(
                "<path:object_id>/download/",
                self.admin_site.admin_view(self.download_view),
                name=f"{app}_{model}_download",
            ),
            path(
                "export-all/",
                self.admin_site.admin_view(self.export_all_view),
                name=f"{app}_{model}_export_all",
            ),
        ]
        return custom + super().get_urls()

    def download_view(self, request, object_id):  # noqa: ANN001, ANN201
        invoice = self.get_object(request, object_id)
        rows = ["sku,quantity,unit_price"]
        rows += [f"{i.sku},{i.quantity},{i.unit_price}" for i in invoice.items.all()]
        response = HttpResponse("\n".join(rows), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="invoice-{invoice.number}.csv"'
        return response

    download_view.short_description = "Download invoice (CSV)"  # type: ignore[attr-defined]

    def export_all_view(self, request):  # noqa: ANN001, ANN201
        rows = ["number,customer"]
        rows += [f"{n},{c}" for n, c in Invoice.objects.values_list("number", "customer")]
        response = HttpResponse("\n".join(rows), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="invoices.csv"'
        return response

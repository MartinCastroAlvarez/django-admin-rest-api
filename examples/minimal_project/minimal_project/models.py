"""Example models: a trivial `Note` (batch / detail actions) plus an
`Invoice` / `LineItem` pair that `InvoiceAdmin` uses to demonstrate editable
inlines, a custom change-form view, and a `get_urls()` download link."""

from __future__ import annotations

from django.db import models


class Note(models.Model):
    """One-line note + archive flag + priority. Just enough for the two
    example actions to do something visible."""

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    priority = models.IntegerField(default=0)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-priority", "-created_at")

    def __str__(self) -> str:
        return self.title


class Invoice(models.Model):
    """A parent record with editable child rows (``LineItem``), a custom
    "recalculate" form, and a "download as CSV" custom view — the three
    features ``InvoiceAdmin`` demonstrates."""

    number = models.CharField(max_length=40, unique=True)
    customer = models.CharField(max_length=200)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.number


class LineItem(models.Model):
    """A child row of an ``Invoice`` — rendered as an editable inline."""

    invoice = models.ForeignKey(Invoice, related_name="items", on_delete=models.CASCADE)
    sku = models.CharField(max_length=40)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self) -> str:
        return f"{self.sku} x{self.quantity}"

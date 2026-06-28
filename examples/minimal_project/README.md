# `examples/minimal_project/` — copy-pasteable starting point

A complete, runnable Django project that consumes `django-admin-rest-api` with the smallest possible wiring. Use it to:

- Evaluate the package before adding it to a real project.
- See the **two-line install** in context (one `INSTALLED_APPS` entry, one `urls.py` include).
- Verify a custom `ModelAdmin` with one `batch` and one `detail` action shows up correctly in the registry.
- See the three "advanced admin" features wired over plain `ModelAdmin` hooks on `InvoiceAdmin`: an **editable inline** (`LineItem`), a **custom change-form view** (`?recalc=1` → `html-fragment`), and **`get_urls()` custom views** surfaced as `custom_views` links.

## Try it locally

```bash
cd examples/minimal_project
python -m pip install django django-admin-rest-api

python manage.py migrate
python manage.py createsuperuser            # username: root, anything else
python manage.py admin_rest_api_check       # smoke-test the install
python manage.py runserver
```

Then:

```bash
# Log into the HTML admin first (browser): http://localhost:8000/admin/
# That gives you a session cookie + CSRF cookie.

# Now exercise the JSON API:
curl -b cookies.txt http://localhost:8000/admin-api/api/v1/registry/ | jq
```

The registry will list `examples_minimal_project.Note` (the test model in
`models.py`) alongside the stock `auth.User` / `auth.Group`. The `Note`
admin declares two actions to demonstrate the [batch vs detail
target dispatch](../../docs/api-contract.md#34-actions-on-the-list--detail--registry-responses):

- `mark_archived` — `(self, request, queryset)` → `target=batch`,
  rendered on the changelist.
- `bump_priority` — `(self, request, obj_id: str)` → `target=detail`,
  rendered on the single-object page.

Both go through the same `actions/<name>/` runner.

It also registers `minimal_project.Invoice`, whose `InvoiceAdmin`
demonstrates the three features a richer admin needs — all via stock
`ModelAdmin` hooks, no package-specific API:

- **Editable inline** — `LineItemInline` lets you add / edit / delete
  child rows in the parent form; the SPA writes them back in the parent
  `PATCH` via the [`"inlines"` block](../../docs/api-contract.md#51-post-apiv1appmodel--create).
- **Custom change-form view** — `change_view` branches on `?recalc=1`
  to render a non-stock template, which the form-spec endpoint serves as
  an `html-fragment` the SPA injects in place.
- **`get_urls()` custom views** — an object-level "Download invoice
  (CSV)" and a changelist-level "Export all invoices", surfaced as
  `custom_views` link descriptors on the detail / registry responses.

## File map

| File                              | Why it's there |
|-----------------------------------|----------------|
| [`manage.py`](manage.py)          | Standard Django entrypoint. |
| [`minimal_project/settings.py`](minimal_project/settings.py) | The two-line install — `django_admin_rest_api` in `INSTALLED_APPS`, that's it. |
| [`minimal_project/urls.py`](minimal_project/urls.py) | One `include()` mounts the JSON API at `/admin-api/`. |
| [`minimal_project/admin.py`](minimal_project/admin.py) | `NoteAdmin` (two action shapes) + `InvoiceAdmin` (editable inline, custom change-form view, `get_urls()` custom views). |
| [`minimal_project/models.py`](minimal_project/models.py) | `Note` + `Invoice` / `LineItem` models. |
| [`minimal_project/templates/admin/minimal_project/invoice/recalc.html`](minimal_project/templates/admin/minimal_project/invoice/recalc.html) | The non-stock change-form template served as an `html-fragment`. |

## Next steps

- Drop the package into your real project — the wiring is identical.
- Read the [README](../../README.md) for the full feature list.
- Read [`docs/api-contract.md`](../../docs/api-contract.md) for the wire shape of every endpoint.
- If something looks off, run `python manage.py admin_rest_api_check` first — it surfaces 90% of install mistakes.

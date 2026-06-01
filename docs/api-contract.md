# Wire contract

This document is the source-of-truth for the JSON wire shape every
endpoint serves. It is stable under semver: a rename, removal, or
type change of a documented field, status code, or URL pattern
requires a **major** version bump.

All paths are mounted under the consumer-chosen prefix at
`api/v1/...` (e.g. `/admin-api/api/v1/registry/`). Authentication is
Django's session cookie; CSRF is enforced by middleware on every
unsafe method.

---

## §1. Common envelopes

### 1.1 Error envelope

Every non-`2xx` response with a JSON body uses this shape:

```json
{"error": {"code": "<machine-readable>", "message": "<human readable>"}}
```

Codes used:

| Code                        | Status | Meaning |
|-----------------------------|--------|---------|
| `not_authenticated`         | 403    | No / expired session cookie. |
| `forbidden`                 | 403    | Authenticated but the `ModelAdmin` permission gate said no. |
| `invalid_credentials`       | 403    | Login: any of unknown user / wrong password / inactive / not staff. Single body deliberately — no enumeration oracle. |
| `not_found`                 | 404    | Unknown model, unknown pk, unknown action name. Deny-by-default — never reveals whether the object exists. |
| `bad_request`               | 400    | Malformed JSON body, missing required key, `pks` over `MAX_ACTION_PKS`, validation cannot run for some other input-shape reason. |
| `validation_failed`         | 400    | `ModelForm.is_valid()` returned False; per-field errors in `error.fields`. |
| `method_not_allowed`        | 405    | HTTP method not implemented by the endpoint. The `Allow` response header lists the permitted methods. |
| `conflict`                  | 409    | Optimistic-concurrency miss on `update`. |

### 1.2 Success cache header

Every successful API response sets `Cache-Control: no-store`. Per-user, permission-gated payloads must never be cached by intermediate proxies or the browser.

---

## §2. `GET /api/v1/registry/`

Returns the full app/model tree the request's user may see, plus the user's identity block.

```json
{
  "mount": "/admin-api/",
  "user": {
    "id": 42,
    "username": "root",
    "display_name": "Root User",
    "is_staff": true,
    "is_superuser": true
  },
  "apps": [
    {
      "name": "Authentication and Authorization",
      "app_label": "auth",
      "verbose_name": "Authentication and Authorization",
      "is_group": false,
      "models": [
        {
          "app_label": "auth",
          "model_name": "user",
          "object_name": "User",
          "verbose_name": "user",
          "verbose_name_plural": "users",
          "permissions": {"view": true, "add": true, "change": true, "delete": true},
          "actions": [
            {
              "name": "delete_selected",
              "label": "Delete selected users",
              "description": "Delete selected users",
              "requires_confirmation": true,
              "target": "batch"
            }
          ]
        }
      ]
    }
  ]
}
```

- `mount` — the path before `api/v1/`, recovered from `request.path` so the consumer doesn't need to repeat it.
- `apps[]` — comes from `AdminSite.get_app_list(request)`. Filtered by `ModelAdmin.has_module_permission` + `has_view_permission` (Django's own gates).
- `actions[]` — see [§3.4](#34-actions-on-the-list--detail--registry-responses) for the descriptor.

---

## §3. `GET /api/v1/<app>/<model>/` — changelist

Pagination, search, filters, sort. Query parameters:

| Param        | Default                          | Meaning |
|--------------|----------------------------------|---------|
| `page`       | `1`                              | 1-indexed page number. |
| `page_size`  | `ModelAdmin.list_per_page`       | Rows per page. Capped at `MAX_PAGE_SIZE` (default 200). |
| `q`          | (none)                           | Forwarded verbatim to `ModelAdmin.get_search_results(request, queryset, term)`. |
| `o`          | `ModelAdmin.ordering`            | Django ordering spec, e.g. `-date_joined,username`. Honors `ModelAdmin.get_sortable_by(request)`. |
| `<filter>`   | (none)                           | Each `ModelAdmin.list_filter` entry exposes its own query param. |

Response:

```json
{
  "count": 1247,
  "show_full_count": true,
  "page": 1,
  "page_size": 25,
  "num_pages": 50,
  "columns": [
    {"name": "username", "label": "Username", "sortable": true, "editable": false, "links": true}
  ],
  "results": [
    {"pk": 1, "label": "root", "values": {"username": "root", "is_active": true}}
  ],
  "actions": [...],
  "filters": [...],
  "search_help_text": ""
}
```

### 3.1 `count` / `show_full_count`

`count` reflects what the consumer's `ModelAdmin.show_full_result_count` would render. When False the count is `null`; the SPA must hide the "X of Y" badge in that case.

### 3.2 `columns[]`

| Key | Meaning |
|-----|---------|
| `name`        | Field name or method name on the admin / model. |
| `label`       | Resolved label (`label_for_field` / `short_description` / verbose name). |
| `sortable`    | `True` if the column appears in `get_sortable_by(request)`. |
| `editable`    | `True` if in `list_editable`. |

The changelist payload also carries a top-level **`list_display_links`** array: the column name(s) the SPA should link to the change page, resolved from `ModelAdmin.get_list_display_links(request, list_display)`. It honors `list_display_links = None` (linking disabled) by emitting `[]`, and the default (link the first column) otherwise. Only string column names round-trip; callable `list_display` entries are dropped.

### 3.3 `results[]`

Each row carries the primary key (`pk`), a display label (`__str__`), and `values` — a map keyed by `column.name`. Each value is one of:

- A plain JSON primitive (`null`, `bool`, `int`, `float`, `str`).
- An ISO-8601 datetime / date / time string.
- A `{"html": "..."}` envelope when the admin returned a `SafeString` (via `format_html` / `mark_safe`). The SPA renders the inner HTML as markup.
- A `{"to": {"app_label": ..., "model_name": ..., "pk": ..., "label": ...}}` envelope for FK / M2M relationships.

### 3.4 `actions[]` (on the list, detail, and registry responses)

```json
{
  "name": "delete_selected",
  "label": "Delete selected users",
  "description": "Delete selected users",
  "requires_confirmation": true,
  "target": "batch"
}
```

- `target = "batch"` — the action callable takes `(modeladmin, request, queryset)` (stock Django). The SPA renders it on the changelist with multi-select.
- `target = "detail"` — the callable takes `(modeladmin, request, obj_id: str)`. The SPA renders it on the detail page only.
- Classification is signature-based; see [§5.4](#54-post-apiv1appmodelactionsname).

### 3.5 `filters[]`

One entry per `ModelAdmin.list_filter`. Each carries the query parameter name, label, and the option set (or `null` when the filter is a search-style autocomplete).

---

## §4. `GET /api/v1/<app>/<model>/<pk>/` — detail

Single-object descriptor for the change page:

```json
{
  "app_label": "auth",
  "model_name": "user",
  "pk": 1,
  "label": "root",
  "permissions": {"view": true, "add": true, "change": true, "delete": true},
  "save_options": {"save_on_top": false, "save_as": false, "save_as_continue": true},
  "password_change": {"available": true, "url": "/admin-api/api/v1/auth/user/1/password/"},
  "fieldsets": [
    {"name": null, "fields": ["username", "password"], "description": null, "classes": []}
  ],
  "fields": {
    "username": {"value": "root", "label": "Username", "type": "string", "readonly": false, ...}
  },
  "inlines": [...],
  "view_on_site_url": null,
  "object_actions": [...],
  "empty_value_display": "-"
}
```

- `password_change.available = true` iff the admin declares `change_password_form` (i.e. `UserAdmin`).
- `object_actions` shares the shape and source of `actions` on the list response — the consumer renders detail-target actions here. Clicks POST to the same runner URL as the changelist actions.
- `fields[*].type` is one of the documented kinds: `string`, `integer`, `decimal`, `float`, `boolean`, `date`, `datetime`, `time`, `duration`, `uuid`, `json`, `array`, `range`, `foreign_key`, `many_to_many`, `file`, `email`, `url`, `image`, `unsupported`.
- `fields[*].widget` is an optional presentational hint mirroring the `ModelAdmin` relation widgets: `radio` (`radio_fields`), `raw_id` (`raw_id_fields`), `shuttle_h` / `shuttle_v` (`filter_horizontal` / `filter_vertical`), `autocomplete` (a relation in `get_autocomplete_fields(request)` **whose target admin declares `search_fields`** — otherwise no hint), `password` / `textarea` (resolved from the bound form widget). Absent when the default control applies. (The form-spec §4.1 carries the same information in its closed `widget.kind` enum.)

### 4.1 `GET /api/v1/<app>/<model>/<pk>/form-spec/` and `…/add/form-spec/` — ModelAdmin form spec

The detail payload (§4) is built from the **model** layer. The form-spec
endpoint is built from the **ModelAdmin** layer: it resolves the *live*
form the legacy `/admin/` change (or add) page would render — honouring
request-aware `get_form(request, obj, change)`, `get_fieldsets(request,
obj)`, `get_readonly_fields(request, obj)`, `formfield_overrides`, custom
`Form` classes, and the admin relation widgets — and maps each field's
resolved widget to a **closed `widget.kind` enum**. The SPA change/add page
and the MCP `admin.form_spec` tool consume the same resolver, so they can
never drift.

- `…/<pk>/form-spec/` → change form for an existing object (`get_form(…,
  change=True)`); gated on per-object `has_view_permission`.
- `…/add/form-spec/` → add form for a new object (`get_form(…,
  change=False, obj=None)`); gated on `has_add_permission`.
- The request's querystring is forwarded into the resolved `request`, so a
  `get_form` that branches on `request.GET` (e.g. `?variant=…`) renders the
  matching form (the `variant` field reflects the resolved `Form` class).

Normal response (`renderer: "form-spec"`):

```json
{
  "renderer": "form-spec",
  "fieldsets": [
    {"title": "Identity", "fields": ["name", "slug"], "field_rows": [["name", "slug"]],
     "classes": ["wide"], "description": null}
  ],
  "fields": {
    "name": {
      "label": "Name", "help_text": "", "required": true, "readonly": false,
      "type": "string",
      "widget": {"kind": "text", "attrs": {"maxlength": 150}},
      "initial": "editors", "errors": []
    },
    "bio": {
      "label": "Bio", "required": false, "readonly": false, "type": "text",
      "widget": {"kind": "custom", "attrs": {"rows": 10},
                 "widget_class": "mypkg.widgets.MarkdownEditor",
                 "template_name": "mypkg/markdown.html"},
      "initial": "", "help_text": "", "errors": []
    }
  },
  "variant": "myapp.forms.GroupForm"
}
```

- `widget.kind` is a **closed enum**: `text`, `textarea`, `number`,
  `email`, `url`, `password`, `hidden`, `checkbox`, `checkbox-multiple`,
  `select`, `select-multiple`, `radio`, `date`, `datetime`, `time`,
  `split-datetime`, `select-date`, `file`, `autocomplete`,
  `autocomplete-multiple`, `raw-id`, `shuttle`, `custom`. A widget with no
  recognised Django ancestor maps to `custom`.
- `widget.attrs` carries the resolved HTML attrs (so `formfield_overrides`
  is visible — e.g. a forced `Textarea`'s `{"rows": 10}`); always
  JSON-scalar values.
- `widget.widget_class` (+ `widget.template_name` when present) is attached
  whenever the widget class lives outside `django.*` — i.e. it came from
  the consumer, `formfield_overrides`, or a third-party library — so a
  consumer-registered SPA renderer (django-admin-react #625 protocol) can
  dispatch on it. `kind` still carries a stock fallback so a client with no
  plugin renders something usable.
- `initial` reuses the §4 value serialisation verbatim (FK → `{id, label}`,
  M2M → `[…]`, a `PasswordInput`-masked value → `null`).
- `errors` is `[]` on a GET; submitting through `POST`/`PATCH` (§5.1/§5.2)
  re-runs the **same** resolved `get_form` through `is_valid()` and returns
  per-field errors under `fields[<name>]` — request-aware validation is
  identical across SPA, MCP, and the legacy admin.
- On the **add** form-spec (`…/add/form-spec/`, `obj=None`) the payload also
  carries `prepopulated_fields` — a `{target: [sources]}` map from
  `ModelAdmin.prepopulated_fields`, restricted to rendered, non-readonly
  targets — so a client can slugify-on-keystroke exactly like the legacy
  add page. (Same shape the add-form schema endpoint `/add/` already emits.)
  Absent / `{}` on the change form-spec.

Escape hatch — when the form can't be faithfully rendered from JSON, the
endpoint returns a pointer to embed the legacy admin page in an iframe for
that one view instead of silently dropping the customisation. Two cases
trigger it:

1. the ModelAdmin sets `change_form_template` / `add_form_template`; or
2. the ModelAdmin overrides `change_view` / `add_view` and that override
   renders a non-standard template for *this* request (e.g. a `?run_custom=1`
   branch returning a hand-rolled `render(...)`). The resolver probes the
   overridden view and falls back to the iframe when the response is not the
   stock `admin/change_form.html`. The querystring is preserved on
   `legacy_url`.

```json
{"renderer": "legacy-iframe", "legacy_url": "/admin/auth/group/1/change/?…"}
```

---

## §5. Writes

### 5.1 `POST /api/v1/<app>/<model>/` — create

Request body: `{"fields": {...}, "inlines": {...}}`. Both keys are optional; the field block omits anything readonly.

Success (`201`):

```json
{"pk": 17, "label": "alice", "redirect": "/admin-api/api/v1/auth/user/17/"}
```

Failure (`400`):

```json
{
  "error": {"code": "validation_failed", "message": "Invalid input."},
  "fields": {"username": ["A user with that username already exists."]}
}
```

### 5.2 `PATCH /api/v1/<app>/<model>/<pk>/` — update

Same request and error shape as create. On success returns the full detail payload (§4) so the SPA can refresh without a second round-trip. Optimistic concurrency: optional `If-Match` header carries a per-row version; mismatch returns `409 conflict`.

### 5.3 `DELETE /api/v1/<app>/<model>/<pk>/` — destroy

Returns `204 No Content` on success. The `LogEntry` is emitted before `delete_model` runs (so `obj.pk` is still available).

### 5.4 `POST /api/v1/<app>/<model>/actions/<name>/`

Run one `ModelAdmin.actions` callable.

Request: `{"pks": [...], "confirmed": <bool>}`. `confirmed` forwards into Django's `delete_selected` two-phase protocol.

Constraints:
- `pks` must be a non-empty list.
- `len(pks)` ≤ `MAX_ACTION_PKS` (default 5000; 0 disables).
- For a `target=detail` action, `len(pks)` MUST equal 1; multi-pk → `400`.

Response (`200`):

```json
{
  "executed": true,
  "action": "delete_selected",
  "pks": [1, 2],
  "messages": [{"level": "info", "message": "2 users deleted."}]
}
```

If the action returns an `HttpResponse` (e.g. an intermediate confirmation page), the envelope is `{"redirect": "<url>", "executed": true, ...}`.

### 5.5 `PATCH /api/v1/<app>/<model>/bulk/`

Apply the same field-value patch to a selection. Request: `{"pks": [...], "fields": {...}}`. Returns per-row `{ok, error}` envelopes. The batch size is capped by the `MAX_BULK_UPDATES` setting (defaults to `MAX_PAGE_SIZE` when unset; `0` disables the cap) — a DoS guard against a single request materialising thousands of forms.

### 5.6 `POST /api/v1/<app>/<model>/<pk>/password/`

JSON mirror of `UserAdmin`'s password-change page. 404 unless the admin declares `change_password_form`. Gated by `has_change_permission`. Body: `{"password1": "...", "password2": "..."}`. Errors map by field name to match the admin form.

---

## §6. Sub-resources on `<pk>`

| Verb  | Path                                                          | Returns |
|-------|---------------------------------------------------------------|---------|
| `GET` | `<app>/<model>/<pk>/history/`                                 | Paginated `LogEntry` timeline. |
| `GET` | `<app>/<model>/<pk>/panel/<name>/`                            | Custom panel: handler return value verbatim. |
| `GET` | `<app>/<model>/<pk>/delete-preview/`                          | Cascade preview before destroy. |
| `GET` | `<app>/<model>/autocomplete/?q=...`                           | Source for `ModelAdmin.autocomplete_fields`. |

### 6.1 History entries

```json
{
  "id": 17,
  "action": "change",
  "action_time": "2026-05-31T12:00:00+00:00",
  "user": {"id": 1, "label": "root"},
  "change_message_human": "Changed Username and Email.",
  "change_message_structured": [{"changed": {"fields": ["username", "email"]}}]
}
```

Sensitive-name redaction: field names matching the package's denylist (`password`, `token`, `secret`, `api_key`, `hash`, `private_key`, `session`, `nonce`, `salt`) are stripped from `change_message_structured.<op>.fields`. `change_message_human` (Django's prose render) is unaffected — Django itself does not put values for sensitive fields there.

---

## §7. Authentication

| Verb  | Path                  | Body                              | Success |
|-------|-----------------------|-----------------------------------|---------|
| `POST`| `api/v1/login/`       | `{"username": "...", "password": "..."}` | `200 {"user": {...}}`. Rotates the session key (session-fixation defense). |
| `POST`| `api/v1/logout/`      | empty                             | `200 {"detail": "logged out"}` |

The single generic `403 invalid_credentials` body is returned for ALL failure modes of login. Django's `ModelBackend.authenticate` runs the password hasher even when the username doesn't exist (dummy-hash run), so response timing does not leak existence either.

Both endpoints require a CSRF token. The consumer is responsible for setting the CSRF cookie before posting to either (e.g. the React shell view does this on the initial GET).

---

## §8. Schema

`GET /api/v1/schema/` returns the OpenAPI 3.1 document describing every endpoint above. Stable under the same semver commitment as the rest of this file. Use it to generate clients in other languages.

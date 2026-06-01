# Security policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security findings.

Instead, open a private vulnerability report using GitHub's
[Security Advisories](https://github.com/MartinCastroAlvarez/django-admin-api/security/advisories/new)
flow, or email the maintainer at `martincastroalvarez@gmail.com` with
the subject `[SECURITY] django-admin-rest-api`.

You can expect:

- An acknowledgement within 5 business days.
- A target patch ETA within 10 business days for issues with a fix
  path. Coordinated disclosure for issues that require one.

## Threat model summary

`django-admin-rest-api` is a JSON wrapper over the Django admin's
existing security model. **It deliberately does not introduce new
auth, permission, validation, or audit surfaces.** Every property
below is delegated to Django itself or to your `ModelAdmin`:

| Surface                | Owner                                                  |
| ---------------------- | ------------------------------------------------------ |
| Authentication         | `django.contrib.auth` (session + `authenticate`)        |
| Per-model authorization| `ModelAdmin.has_view/add/change/delete_permission`      |
| Per-object authorization| Same — invoked with the resolved instance              |
| Per-field validation   | `ModelAdmin.get_form(request, obj)` (i.e. your `ModelForm`) |
| Search                 | `ModelAdmin.get_search_results`                         |
| Filters                | `ModelAdmin.list_filter`                                |
| Audit log              | Django's `LogEntry`                                     |
| CSRF                   | `django.middleware.csrf.CsrfViewMiddleware`             |
| Session expiry         | `SessionMiddleware`                                     |

If your HTML admin is correctly configured, this API inherits its
posture. If your HTML admin is misconfigured, this API surfaces the
same exposure.

## Hardening invariants (enforced in CI / pre-commit)

These rules are enforced by the local pre-commit hooks
(`.pre-commit-config.yaml`) — a violation fails the commit and the
build:

- No `@csrf_exempt` anywhere in the package.
- No `Model.objects.all(...)` / `Model.objects.filter(...)` in the
  `api/` subpackage — all querysets must originate from
  `ModelAdmin.get_queryset(request)` so consumer overrides apply.
- No `user.has_perm(...)` direct calls in the `api/` subpackage —
  permission checks must go through `ModelAdmin.has_*_permission`.
- No partial token redactions (`ghp_…XYZ`-style) in source files —
  the only way to fail this hook is to actually paste a real or
  partial token, which gitleaks then catches.

## Defaults that exist as DoS guards

- `MAX_PAGE_SIZE` (default `200`) hard-caps the `?page_size` query
  parameter on list endpoints, regardless of the model's
  `list_per_page`. Override only if your dataset genuinely supports
  it and you have monitoring for slow queries.
- `MAX_BULK_UPDATES` caps the number of rows in a single
  `PATCH .../bulk/` batch. It is single-sourced: when unset it tracks
  `MAX_PAGE_SIZE` (so lowering `MAX_PAGE_SIZE` for DoS reasons tightens
  the bulk cap too), and `0` disables it.
- Bulk endpoints (`bulk`, `actions`, `delete-preview`) apply
  the same per-object permission gate over the selection — there is
  no "skip permissions for batches" code path.

## Security logging

The package emits one structured record on the dedicated
`django_admin_rest_api.security` logger at each authorization-denial
boundary — a 403 permission/session-expiry denial
(`api/permissions.forbidden_response`) and a failed login
(`api/views/auth`). Each record carries `{user, path, method, decision}`,
where `user` is the surrogate pk (or `"anon"`) and `decision` is one of
`forbidden` / `session_expired` / `login_failed`. The password and any
other request-body PII are **never** logged. Wire the logger into your
project's `LOGGING` config to alert on credential-stuffing,
permission-probing, and IDOR-scan patterns:

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"security": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django_admin_rest_api.security": {
            "handlers": ["security"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
```

Successful (allowed) requests are intentionally not logged here — only
denials — so the channel stays signal-rich for alerting.

## Cross-references

- Upstream threat model:
  <https://github.com/MartinCastroAlvarez/django-admin-react/blob/main/SECURITY.md>
  (the API surface and guarantees are identical).

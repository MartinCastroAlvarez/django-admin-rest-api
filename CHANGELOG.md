# Changelog

All notable changes to **django-admin-rest-api** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.3] — 2026-05-29

### Added
- **Per-object action runner.** New endpoint
  `POST /api/v1/<app>/<model>/<pk>/action/<name>/` runs one
  `django-object-actions`-style change-page action against a single
  object, mirroring the legacy admin's per-object action surface.
  Action discovery is duck-typed on
  `ModelAdmin.get_change_actions(request, object_id, form_url)`, so
  the package stays free of a runtime dep on a specific third-party
  extension; admins exposing the same shape via any other mechanism
  work without configuration.
- **Descriptor on the detail response.** The detail endpoint
  (`GET /api/v1/<app>/<model>/<pk>/`) now includes an
  `object_actions: [{name, label, description}, ...]` field so a
  client can render one button per action. Empty list for admins
  that don't expose `get_change_actions` — zero overhead for the
  99% path.

### Behavior parity with the HTML admin
- The runner gates on `is_admin_user` + per-object
  `has_change_permission` (same as every other change-shaped
  endpoint). Object resolves through `ModelAdmin.get_queryset(request)`,
  so an action can never reach a row the user couldn't already see.
- Action name re-validation: the URL path's `<name>` must appear in
  the admin's own `get_change_actions(...)` list for the resolved
  user + object, not just exist as a method. Prevents URL-poking
  past a `get_change_actions`-based row-level filter.
- Messages queued by the action via `ModelAdmin.message_user` are
  drained into the response envelope (`messages: [{level, message}]`)
  so the client can toast them without parsing HTML.
- An action returning an `HttpResponse` (e.g. a redirect to a
  confirmation page) surfaces as `{redirect: "<url>"}` in the
  envelope.
- Runs inside a transaction so a raise rolls the mutation back
  cleanly — same posture as the changelist actions view.

## [1.0.2] — 2026-05-28

### Infrastructure
- **PyPI publishing now runs through GitHub Actions.** A new
  `.github/workflows/release.yml` triggers on `v*` tag push, rebuilds
  the sdist + wheel from the tagged source, and uploads via twine in
  a `pypi` GitHub Environment. Every release from now on appears in
  the repo's Deployments tab and inherits the environment's
  protection rules. The `PYPI_API_TOKEN` secret lives in the
  environment, not at the repo level, so PR runs from forks cannot
  read it.

### Behavior
- No code change. The package surface and tests are identical to 1.0.1.

## [1.0.1] — 2026-05-28

### Fixed
- **Django 5.0 compatibility.** The 1.0.0 shim for `log_deletion`
  assumed `ModelAdmin.log_deletions` (plural) existed on every
  supported Django, but Django 5.0 only ships the singular
  `log_deletion`. The shim now uses `hasattr` to prefer the plural
  form (5.1+) and falls back to the singular form on 5.0.

### Changed
- **CI matrix expanded to Django 6.0.** The job now runs the cross
  product of Python 3.10–3.13 × Django 5.0–6.0, excluding the two
  cells Django 6.0 itself does not support (py3.10/3.11).
- **`ruff format` removed from the CI lint job and the pre-commit
  config.** `ruff format` and `black` disagreed on a handful of
  multi-line `assert` cases, so the lint job would fail no matter
  which formatter ran last. `black` is now the single source of
  truth for formatting; `ruff` keeps its `check` (lint) role.
- Re-formatted 9 test files + 1 source file (`api/views/recent_actions.py`)
  to match `black`.

## [1.0.0] — 2026-05-28

First stable release.

The JSON wire contract is now stable. Subsequent changes that rename,
remove, or change the shape of any field, status code, or URL pattern
will require a major version bump (semver).

### Carried-over from 0.1.0a0 – 0.1.0a2
- Full JSON REST API surface over `ModelAdmin`: registry, schema,
  list / detail / create / update / destroy, bulk-update,
  delete-preview, autocomplete, actions, history, recent-actions,
  login / logout, password.
- 520 tests, green on Django 5.0 / 5.1 / 5.2 / 6.0 and Python 3.10 – 3.13.
- Same lint + security tooling as the upstream React repo: ruff,
  black, isort, flake8, pylint, mypy, bandit, pip-audit, gitleaks,
  plus pygrep house rules (no `@csrf_exempt`, no
  `Model.objects.all/filter` in `api/`, no `user.has_perm` in
  `api/`).
- Plug-and-play install (one `INSTALLED_APPS` entry, one `urls.py`
  include).

### Compatibility
- Python 3.10 – 3.13
- Django 5.0 – 6.0

## [0.1.0a2] — 2026-05-28

### Changed
- Made API docstrings frontend-agnostic. The package was extracted
  from `django-admin-react`, so 105 docstring / inline-comment
  references used the word "SPA" (the React single-page app) when
  describing what the consumer of the API does with the response.
  Now that the package is the wire surface for *any* client (the
  React SPA, the forthcoming MCP server, or any other consumer),
  those comments use the generic term "client". Two callouts
  (`api/views/schema.py`, `api/views/auth.py`) name the React SPA
  + MCP server explicitly where the original phrasing referenced a
  React-specific concept. Closes #2.

### Behavior
- No behavior change. Comments / docstrings only.

## [0.1.0a1] — 2026-05-28

### Fixed
- Django 6.0 compatibility for the destroy / bulk-delete code paths
  (`api/writes.py:log_deletion`). Django 6.0 renamed
  `ModelAdmin.log_deletion` → `ModelAdmin.log_deletions`; we now
  prefer the 6.x name when present and fall back to the legacy name.
  Closes #1.
- README: the password endpoint row used to claim the URL was
  `set-password/` with a "set_password permission gate". The actual
  URL (`api/urls.py:161`) is `password/` and the gate is
  `has_change_permission` — same as any other change. README updated
  to match the code (the endpoint itself is unchanged: it is the JSON
  mirror of Django `UserAdmin`'s password-change page,
  `AdminPasswordChangeForm` + `AUTH_PASSWORD_VALIDATORS` +
  `set_password`).

## [0.1.0a0] — 2026-05-28

Initial pre-alpha extraction of the JSON API from
[django-admin-react](https://github.com/MartinCastroAlvarez/django-admin-react)
into a standalone, frontend-agnostic Django app.

### Added
- JSON endpoints for list / detail / create / update / delete /
  bulk-update / delete-preview / autocomplete / actions / history /
  recent-actions / login / logout / password / schema / registry,
  matching the contract previously shipped inside `django-admin-react`.
- `DJANGO_ADMIN_REST_API` settings namespace with sane defaults
  (`DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE`, `ADMIN_SITE`, etc.).
- Same lint + security tooling as the upstream React app: ruff, black,
  isort, flake8, pylint, mypy, bandit, pip-audit, gitleaks.

### Notes
- This release is API-only — there is no UI. Pair with
  [django-admin-react](https://pypi.org/project/django-admin-react/) for
  a SPA frontend, or with the forthcoming `django-admin-mcp` for an MCP
  surface over the same endpoints.

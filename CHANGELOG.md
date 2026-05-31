# Changelog

All notable changes to **django-admin-rest-api** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.10] — 2026-05-31

### Added
- **`docs/api-contract.md`** (#35) — the full wire-contract reference
  the rest of the docstrings have referenced. Every endpoint, every
  envelope, every status code, with explicit semver commitment on
  the documented surface.
- **`examples/minimal_project/`** (#36) — a copy-pasteable runnable
  Django project that consumes the package with the smallest wiring.
  Includes one `ModelAdmin` with both a `batch` and a `detail` action
  so a new consumer can see the signature dispatch in action without
  any custom build.

### Documentation
- **Rate-limit recipe in `README.md`** (#40) — both `django-axes`
  (lockout) and `django-ratelimit` (request/window) recipes for
  protecting the auth + password endpoints in production. Moved out
  of `SECURITY.md`'s "out of scope" footnote and into the main flow.
- Pointer to `examples/minimal_project/` added near the top of the
  install section.

### Behavior
- Doc / examples only. No code change.

## [1.0.9] — 2026-05-31

### Added
- **Three Django system checks** that surface common install mistakes
  at `manage.py runserver` / `manage.py check` time rather than as a
  500 on the first request:
  - **W001** (#31) — warns when a settings attribute starts with
    `DJANGO_ADMIN_REST_API_` but isn't the canonical dict
    (catches typos like `DJANGO_ADMIN_REST_API_CONFIG`).
  - **E001** (#32) — errors when `ADMIN_SITE` doesn't resolve to an
    `AdminSite` instance.
  - **W002** (#33) — warns for each required Django middleware
    (`CsrfViewMiddleware`, `SessionMiddleware`, `AuthenticationMiddleware`)
    missing from `settings.MIDDLEWARE`.
- **Auto-created GitHub Releases** on every tag push. The publish
  workflow now extracts the matching CHANGELOG section and creates
  the GitHub Release (with `--latest` for stable versions, `--prerelease`
  for `aN` / `bN` / `rcN` suffixes) so the repo's Releases panel
  stays in sync with PyPI.

### Behavior
- No breaking change. The checks emit warnings (not errors) where the
  consumer might have a legitimate equivalent.

## [1.0.8] — 2026-05-31

### Changed
- **`PanelEndpointsMixin` is no longer required** to register panels
  (#34). The runtime resolves panels via plain
  `getattr(model_admin, "panels", {})` regardless of whether the
  mixin is mixed in. Declare `panels = {...}` directly on any
  `ModelAdmin` — the convention is now plain Django. The mixin
  class is kept as a no-op shim for backward compatibility; consumers
  who subclass it get a single `DeprecationWarning` at class-definition
  time. It will be removed in a future major release.

### Added
- **`python manage.py admin_rest_api_check`** — smoke-test the
  install (#37). Prints a one-screen health summary: the configured
  `ADMIN_SITE` resolves, the three required middleware classes are
  present, and every registered `ModelAdmin` is listed with its
  action count (broken down by `batch` / `detail` target). Exits
  non-zero on any problem — usable as a CI / deploy preflight.

### Behavior
- No breaking change. Existing code that subclassed
  `PanelEndpointsMixin` keeps working (with one deprecation warning).

## [1.0.7] — 2026-05-31

### Infrastructure
- **Publishing switched to PyPI Trusted Publishing (OIDC).** The
  release workflow no longer relies on a long-lived `PYPI_API_TOKEN`
  secret — PyPI mints a short-lived credential per publish based on
  GitHub's OIDC token. The workflow file is renamed
  `.github/workflows/release.yml` → `.github/workflows/publish.yml`
  to match the trust binding registered on pypi.org, and the obsolete
  `PYPI_API_TOKEN` environment secret has been removed.

### Security
- **Actions runner now caps the number of pks per call** (#41). New
  setting `MAX_ACTION_PKS` (default `5000`) on the
  `DJANGO_ADMIN_REST_API` dict; an action POST with more pks than the
  cap returns `400`. Set to `0` (or any non-positive value) to
  disable. Mirrors `MAX_PAGE_SIZE`'s DoS-guard posture on the list
  endpoint.
- **History endpoint's `change_message_structured` now redacts
  sensitive field names** (#42). Django's structured change message
  lists which fields changed by NAME (`["password", "email"]`); names
  matching the package's sensitive-name denylist are stripped from
  the wire so the audit log can't be used as an oracle for which
  sensitive fields were touched. Field values are not in Django's
  structured payload, so no value redaction is needed. `change_message_human`
  (Django's prose render) is unaffected — Django itself does not put
  values in it for sensitive fields.

### Behavior
- No breaking change. Both settings keep working without consumer
  action; the cap default is large enough that real admin workflows
  are not affected.

## [1.0.6] — 2026-05-29

### Added
- **`target` on every action descriptor.** The action payload exposed
  on the registry, list, and detail responses now carries a
  ``"target": "batch" | "detail"`` field. The classifier inspects the
  registered callable's signature:
    - third parameter named ``queryset`` / ``qs`` OR annotated
      ``QuerySet`` → ``"batch"`` (Django's stock changelist action shape)
    - third parameter named ``obj_id`` / ``object_id`` / ``pk`` / ``id``
      OR annotated ``str`` / ``int`` / ``Model`` subclass →
      ``"detail"`` (single-object shape)
    - anything else → ``"batch"`` (safe default for stock Django).
  The SPA renders ``batch`` actions on the changelist and ``detail``
  actions on the single-object page; one declaration, the surface is
  chosen by signature.
- **Runner dispatch by ``target``.** The existing
  ``POST /api/v1/<app>/<model>/actions/<name>/`` endpoint now calls
  ``batch`` actions with the user-narrowed ``QuerySet`` (unchanged) and
  ``detail`` actions with ``str(obj.pk)`` (new). A ``detail`` action
  POST must pass exactly one entry in ``pks``; multi-pk requests return
  ``400`` so a consumer can't accidentally invoke a single-object action
  across a selection.

### Behavior
- Backward-compatible. Existing stock Django actions
  ``(modeladmin, request, queryset)`` keep their ``"batch"``
  classification and identical runner call shape.

## [1.0.5] — 2026-05-29

### Removed
- **Reverted the `object_actions` / `django-object-actions` integration
  shipped in 1.0.3** (#603, revised). The wrong choice: actions belong
  to Django's stock `ModelAdmin.actions` API, not to a third-party
  extension point. Consumers should not have to declare actions twice
  (once on `actions`, once on `change_actions`) just to make the
  React detail page show buttons.

### Added
- **`actions` on every model in the registry response.** The registry
  endpoint now ships, per model, the list of actions Django's
  `ModelAdmin.get_actions(request)` returns — the same shape the list
  response has long exposed (`{name, label, description,
  requires_confirmation}`). One source of truth: declare the action
  on your `ModelAdmin` once, see it in the registry, the list
  response, and the detail response.
- **`object_actions` on the detail response now sources from the same
  `ModelAdmin.actions` list.** A consumer's detail-page button clicks
  POST to the existing changelist runner
  (`/api/v1/<app>/<model>/actions/<name>/`) with `pks=[<this pk>]`.
  No new endpoint, no `django-object-actions` dependency.

### Behavior
- No new endpoints; the per-object runner URL added in 1.0.3 has been
  removed (it was never released as a stable surface).

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

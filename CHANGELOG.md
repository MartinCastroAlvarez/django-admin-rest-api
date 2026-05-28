# Changelog

All notable changes to **django-admin-rest-api** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

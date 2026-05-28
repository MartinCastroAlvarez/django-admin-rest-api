# Changelog

All notable changes to **django-admin-rest-api** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

# Contributing to django-admin-rest-api

Thanks for the interest! A few rules keep this codebase fast to ship,
secure to deploy, and honest about its scope.

## The one rule

**This library only exposes Django admin behavior over JSON.**
It does not introduce new authentication, new permissions, new
validation, new audit, or new business logic. If a PR adds behavior
that isn't already in Django's HTML admin or `ModelAdmin`, the PR is
out of scope — open it against
[`django-admin-react`](https://github.com/MartinCastroAlvarez/django-admin-react)
(if it's UX) or your own project (if it's domain logic) instead.

## Local setup

```bash
git clone https://github.com/MartinCastroAlvarez/django-admin-api
cd django-admin-api
poetry install
poetry run pre-commit install
```

## Quality gate (must pass locally before opening a PR)

```bash
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
poetry run black --check .
poetry run isort --check-only .
poetry run mypy django_admin_rest_api
poetry run bandit -c pyproject.toml -r django_admin_rest_api
poetry run pip-audit
```

The pre-commit hook runs a security-critical subset on every commit
(gitleaks, bandit, the `api/` invariants). CI runs the full matrix on
Python 3.10–3.13 × Django 5.0–5.2.

## How to propose a change

1. **Open an issue first** for anything non-trivial. We use issues
   (not PR descriptions) as the design conversation.
2. **One concern per PR.** Refactors and behavior changes in the same
   PR are a code review trap. Split them.
3. **Tests are not optional.** Every new endpoint, every new
   serializer branch, every new permission gate needs a test. If you
   can't write one, the change is not done.
4. **No `# type: ignore` without a comment explaining why.**
5. **Comments should explain why, not what.** Don't write
   "increments x by 1" — write the reason the increment exists.

## Security

If you've found a security issue, **do not open a public PR or
issue.** See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contribution is licensed under the
MIT license (see [LICENSE](LICENSE)).

## What

(One sentence on what this PR changes.)

## Why

(The motivation — link the issue.)

## Behavior parity with Django admin

- [ ] This PR does not introduce new auth, new permissions, new
      validation, or new business logic that isn't already in
      `django.contrib.admin` / `ModelAdmin`.
- [ ] If it changes a permission gate, the same gate exists in the
      HTML admin (link the Django source).
- [ ] If it changes a serializer, the same field render exists in the
      HTML admin (link the Django widget).

## Tests

- [ ] New / changed behavior is covered by tests.
- [ ] `poetry run pytest` is green locally.
- [ ] `poetry run ruff check . && poetry run black --check . && poetry run mypy django_admin_rest_api && poetry run bandit -c pyproject.toml -r django_admin_rest_api` is green locally.

## Security

- [ ] No new endpoint is `@csrf_exempt`.
- [ ] No `Model.objects.all/filter` introduced in `api/`.
- [ ] No `user.has_perm(...)` direct call introduced in `api/`.
- [ ] No secrets, tokens, or `.env` values added.

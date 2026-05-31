"""django-admin-rest-api — a JSON REST API for the Django admin.

A frontend-agnostic Django app that exposes every ``ModelAdmin``
through JSON endpoints (list, detail, create, update, delete, actions,
history, autocomplete, …) using the **same permissions and the same
ModelAdmin source of truth** as the HTML admin.

This package adds no new features and no new permissions: it is the
wire surface that lets clients like
`django-admin-react <https://pypi.org/project/django-admin-react/>`_
and the forthcoming ``django-admin-mcp`` drive the Django admin over a
clean JSON contract.

See ``README.md`` for the install + URL wiring, and
``docs/api-contract.md`` (in the repo) for the wire shape.
"""

__version__ = "1.0.7"

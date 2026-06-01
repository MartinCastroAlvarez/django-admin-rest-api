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

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Derived from the installed distribution metadata (single source of truth:
# ``pyproject.toml``). The previous hand-maintained static string drifted
# stale across several releases (it read ``1.1.1`` at the 1.3.0 release), so
# we recover by reading the real installed version here. Trade-off: an
# *editable* install (``pip install -e .``) reports ``0.0.0`` until
# reinstalled — acceptable next to a version string that silently lies.
try:
    __version__ = _pkg_version("django-admin-rest-api")
except PackageNotFoundError:  # pragma: no cover — editable install before build
    __version__ = "0.0.0"

"""Django AppConfig for django_admin_rest_api.

Registering this AppConfig in the consumer's ``INSTALLED_APPS`` is the
only side effect of adding the package. The real wiring (URLs, API
views) is opt-in via the consumer's own ``urls.py``.
"""

from django.apps import AppConfig


class DjangoAdminRestApiConfig(AppConfig):
    """Django app config — the only side effect of adding the package.

    The four attributes are the standard Django ``AppConfig`` contract:

    - ``name`` — Python import path; required by Django's app registry.
    - ``label`` — short identifier used in migrations and admin URLs.
    - ``verbose_name`` — human-readable name shown in the admin index.
    - ``default_auto_field`` — bigint primary keys for any future models
      the package adds (none today, but pinning the default avoids a
      Django warning and locks the choice in for forwards compat).
    """

    name = "django_admin_rest_api"
    label = "django_admin_rest_api"
    verbose_name = "Django Admin REST API"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:  # pragma: no cover — registration shim
        """Wire the package's system checks into Django on app load."""
        # Importing the module is enough — the `@register` decorators
        # at module-level wire the checks into Django's system check
        # framework. See `system_checks.py` for the catalogue.
        from django_admin_rest_api import system_checks  # noqa: F401

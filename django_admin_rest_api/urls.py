"""Top-level URL configuration for django_admin_rest_api.

Mount this at any prefix you like:

    # your-project/urls.py
    from django.urls import include, path

    urlpatterns = [
        path("admin/", admin.site.urls),
        path("admin-api/", include("django_admin_rest_api.urls")),
    ]

The JSON endpoints then live under ``<your-prefix>/api/v1/...``. See
``docs/api-contract.md`` for the wire shape.
"""

from __future__ import annotations

from django.urls import include
from django.urls import path

app_name = "django_admin_rest_api"

urlpatterns: list = [
    # JSON endpoints. No URL namespace: the client builds these URLs
    # from the wire contract, not via Django's ``reverse()``.
    path("api/v1/", include("django_admin_rest_api.api.urls")),
]

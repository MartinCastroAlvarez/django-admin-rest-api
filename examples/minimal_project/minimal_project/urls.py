"""URL config for the minimal example project.

The ONE line that adds the JSON API is the second `path()` below.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
    # The JSON API: every endpoint documented in docs/api-contract.md
    # is now reachable under /admin-api/api/v1/...
    path("admin-api/", include("django_admin_rest_api.urls")),
]

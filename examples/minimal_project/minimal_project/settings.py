"""Minimal settings for the example project.

This is intentionally short: the only line that differs from a vanilla
Django project is the `django_admin_rest_api` entry in `INSTALLED_APPS`.

NEVER use this settings file as-is in production — `SECRET_KEY`,
`DEBUG`, and `ALLOWED_HOSTS` are tuned for local exploration only.
"""

from __future__ import annotations

import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "example-only-" + secrets.token_urlsafe(16)  # noqa: S105
DEBUG = True
ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # The ONE line that adds the JSON API surface.
    "django_admin_rest_api",
    # Your apps:
    "minimal_project",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "minimal_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
USE_TZ = True
TIME_ZONE = "UTC"
LOGIN_URL = "/admin/login/"

# Optional — every key has a sensible default, so you can leave this
# block out entirely. Shown here for discoverability.
DJANGO_ADMIN_REST_API = {
    # "ADMIN_SITE": "django.contrib.admin.site",
    # "DEFAULT_PAGE_SIZE": 25,
    # "MAX_PAGE_SIZE": 200,
    # "MAX_ACTION_PKS": 5000,
    # "ENABLE_PROFILING": False,
}

#!/usr/bin/env python
"""Django's standard `manage.py` for the minimal example project."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "minimal_project.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Couldn't import Django. Run `pip install django django-admin-rest-api` "
            "from inside this directory first."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

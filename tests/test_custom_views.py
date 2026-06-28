"""Tests for ``get_urls()`` custom-view surfacing (#439).

A ``ModelAdmin`` may register bespoke pages via ``get_urls()`` (a report, an
importer, a "download schema.sql" link). The package surfaces them as
``{name, label, url, level}`` entries so the SPA's ``CustomViewsMenu`` can
link out: object-level views (route captures ``object_id``) on the **detail**
response, model-wide views on the **registry** model entry. Stock CRUD routes
(change / add / changelist / delete / history) are always excluded.

The ``JobAdmin`` fixture (``tests/test_project/jobs/admin.py``) declares both:
an object-level ``download_report_view`` (with a ``short_description``) and a
changelist-level ``export_all_view`` (humanized-name label).
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import Client

from django_admin_rest_api.api.custom_views import custom_views_payload
from tests.test_project.jobs.models import Job

REGISTRY_URL = "/admin-api/api/v1/registry/"


def _job_admin() -> admin.ModelAdmin:
    return admin.site._registry[Job]


# --------------------------------------------------------------------------- #
# Unit — custom_views_payload                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_changelist_level_surfaces_only_model_wide_views() -> None:
    views = custom_views_payload(_job_admin(), admin.site, level="changelist")
    names = {v["name"] for v in views}
    assert "jobs_job_export_all" in names
    # Object-level + stock CRUD routes never leak into the changelist set.
    assert "jobs_job_download_report" not in names
    assert not (names & {"jobs_job_change", "jobs_job_add", "jobs_job_changelist"})
    export = next(v for v in views if v["name"] == "jobs_job_export_all")
    assert export["level"] == "changelist"
    assert export["url"] == "/admin/jobs/job/export-all/"
    # No short_description on the view → humanized route-name suffix.
    assert export["label"] == "Export All"


@pytest.mark.django_db
def test_object_level_reverses_with_pk_and_uses_short_description() -> None:
    job = Job.objects.create(name="nightly")
    views = custom_views_payload(_job_admin(), admin.site, level="object", obj=job)
    names = {v["name"] for v in views}
    assert "jobs_job_download_report" in names
    assert "jobs_job_export_all" not in names  # changelist view excluded here
    download = next(v for v in views if v["name"] == "jobs_job_download_report")
    assert download["level"] == "object"
    assert download["url"] == f"/admin/jobs/job/{job.pk}/download-report/"
    # short_description on the view drives the label.
    assert download["label"] == "Download report (CSV)"


@pytest.mark.django_db
def test_object_level_without_obj_is_empty() -> None:
    assert custom_views_payload(_job_admin(), admin.site, level="object", obj=None) == []


@pytest.mark.django_db
def test_admin_without_custom_urls_yields_nothing() -> None:
    # Group's stock ModelAdmin registers no custom get_urls routes.
    from django.contrib.auth.models import Group

    group_admin = admin.site._registry[Group]
    assert custom_views_payload(group_admin, admin.site, level="changelist") == []
    grp = Group.objects.create(name="ops")
    assert custom_views_payload(group_admin, admin.site, level="object", obj=grp) == []


# --------------------------------------------------------------------------- #
# Integration — registry + detail endpoints                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_registry_entry_carries_changelist_custom_views(superuser_client: Client) -> None:
    payload = superuser_client.get(REGISTRY_URL).json()
    job_entry = next(
        m for app in payload["apps"] for m in app["models"] if m["model_name"] == "job"
    )
    names = {v["name"] for v in job_entry.get("custom_views", [])}
    assert names == {"jobs_job_export_all"}


@pytest.mark.django_db
def test_registry_entry_omits_key_when_no_custom_views(superuser_client: Client) -> None:
    payload = superuser_client.get(REGISTRY_URL).json()
    group_entry = next(
        m for app in payload["apps"] for m in app["models"] if m["model_name"] == "group"
    )
    # Lean registry: the key is absent entirely for admins with no custom routes.
    assert "custom_views" not in group_entry


@pytest.mark.django_db
def test_detail_response_carries_object_custom_views(superuser_client: Client) -> None:
    job = Job.objects.create(name="nightly")
    payload = superuser_client.get(f"/admin-api/api/v1/jobs/job/{job.pk}/").json()
    assert "custom_views" in payload
    names = {v["name"] for v in payload["custom_views"]}
    assert names == {"jobs_job_download_report"}
    download = payload["custom_views"][0]
    assert download["url"].endswith(f"/{job.pk}/download-report/")
    assert download["level"] == "object"

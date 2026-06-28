"""``JobAdmin`` — the cross-repo custom-form fixture (#59 / #70 / react #659).

Exercises only documented ``ModelAdmin`` hooks — no django-admin-react /
-rest-api / -mcp-specific API — so that "if this works, any legacy admin
works":

* ``formfield_for_dbfield`` — request-aware widget override on ``metadata``
  (Path A: the stock change form, fully describable by the JSON form-spec).
* ``run_with_custom_steps`` action — redirects to ``?run_custom=1``.
* ``change_view`` — branches on ``request.GET`` to a custom view.
* ``run_custom_view`` — renders a hand-rolled dual-listbox template
  (Path B: the JSON form-spec can't reproduce it → server-rendered
  ``html-fragment``, #75).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import path
from django.urls import reverse

from tests.test_project.jobs.models import Job


def get_step_registry() -> list[dict]:
    """Return the catalogue of pipeline steps a Job can run.

    Plain data so the fixture has something for the dual-listbox to render;
    a real integrator would source this from their own app.
    """
    return [
        {"name": "fetch", "label": "Fetch inputs", "default_order": 1, "is_default": True},
        {"name": "validate", "label": "Validate", "default_order": 2, "is_default": True},
        {"name": "transform", "label": "Transform", "default_order": 3, "is_default": True},
        {"name": "dry_run", "label": "Dry run", "default_order": 4, "is_default": False},
        {"name": "notify", "label": "Notify", "default_order": 5, "is_default": False},
        {"name": "archive", "label": "Archive", "default_order": 6, "is_default": False},
    ]


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("name", "status")
    actions = ["run_with_custom_steps"]

    # 1) request-aware widget override on a single DB field (Path A).
    def formfield_for_dbfield(self, db_field, request, **kwargs):  # noqa: ANN001
        if db_field.name == "metadata":
            kwargs["widget"] = admin.widgets.AdminTextareaWidget(attrs={"class": "vLargeTextField"})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    # 2) action that redirects to the ?run_custom=1 variant of the change page.
    @admin.action(description="Run (Custom)")
    def run_with_custom_steps(self, request, queryset):  # noqa: ANN001
        if queryset.count() != 1:
            self.message_user(request, "Pick exactly one row.", level=messages.ERROR)
            return None
        obj = queryset.get()
        return HttpResponseRedirect(
            reverse("admin:jobs_job_change", args=[obj.pk]) + "?run_custom=1"
        )

    # 3) change_view override branching on request.GET.
    def change_view(self, request, object_id, form_url="", extra_context=None):  # noqa: ANN001
        if request.GET.get("run_custom") == "1":
            return self.run_custom_view(request, object_id)
        return super().change_view(request, object_id, form_url, extra_context)

    # 4) custom view rendering a custom template — not a ModelForm / fieldsets.
    def run_custom_view(self, request, object_id):  # noqa: ANN001
        obj = self.get_object(request, object_id)

        if request.method == "POST":
            selected = request.POST.getlist("selected_steps")
            if not selected:
                messages.error(request, "Pick at least one step.")
                return redirect(request.get_full_path())
            messages.success(request, f"Queued {' → '.join(selected)}")
            return redirect("admin:jobs_job_change", object_id)

        all_steps = get_step_registry()
        selected_steps = sorted(
            (s for s in all_steps if s["is_default"]),
            key=lambda s: s["default_order"],
        )
        available_steps = [s for s in all_steps if not s["is_default"]]

        context = dict(
            self.admin_site.each_context(request),
            title=f"Configure Step Sequence: {obj.name}",
            item_type="Job",
            item_name=obj.name,
            obj=obj,
            available_steps=available_steps,
            selected_steps=selected_steps,
            all_steps=sorted(all_steps, key=lambda s: s["default_order"]),
            back_url=reverse("admin:jobs_job_changelist"),
            cancel_url=reverse("admin:jobs_job_change", args=[obj.pk]),
            opts=self.model._meta,
            preserved_filters=self.get_preserved_filters(request),
        )
        return render(request, "admin/jobs/job/run_custom.html", context)

    # 5) get_urls() custom routes (#439). The generic twin of a consumer's
    #    "download schema.sql" link / report page: an OBJECT-level download
    #    (route captures ``object_id`` → surfaced on the detail response) and
    #    a CHANGELIST-level export (no capture → surfaced on the registry).
    #    The SPA can't render these, so it links out via ``CustomViewsMenu``.
    def get_urls(self):  # noqa: ANN201
        app, model = self.opts.app_label, self.opts.model_name
        custom = [
            path(
                "<path:object_id>/download-report/",
                self.admin_site.admin_view(self.download_report_view),
                name=f"{app}_{model}_download_report",
            ),
            path(
                "export-all/",
                self.admin_site.admin_view(self.export_all_view),
                name=f"{app}_{model}_export_all",
            ),
        ]
        return custom + super().get_urls()

    def download_report_view(self, request, object_id):  # noqa: ANN001, ANN201
        obj = self.get_object(request, object_id)
        name = obj.name if obj else object_id
        body = f"-- report for job {name}\nstatus,{getattr(obj, 'status', '')}\n"
        response = HttpResponse(body, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="job-{object_id}.csv"'
        return response

    # ``short_description`` drives the SPA link label for the object-level
    # view; the changelist ``export_all_view`` has none, so its label falls
    # back to the humanized route name ("Export All") — both label paths are
    # exercised by the tests.
    download_report_view.short_description = "Download report (CSV)"  # type: ignore[attr-defined]

    def export_all_view(self, request):  # noqa: ANN001, ANN201
        body = "-- all jobs\n" + "\n".join(Job.objects.values_list("name", flat=True))
        response = HttpResponse(body, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="all-jobs.csv"'
        return response

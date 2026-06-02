from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Test-only app exercising the request-driven custom-view renderer.

    ``JobAdmin`` overrides ``change_view`` to branch on ``?run_custom=1`` and
    render a hand-rolled template (no ``change_form_template`` attribute), so
    the form-spec resolver must *probe* the view to detect the custom render,
    render it server-side, and return the ``html-fragment`` payload (#59 / #75
    / cross-repo fixture)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "tests.test_project.jobs"
    label = "jobs"

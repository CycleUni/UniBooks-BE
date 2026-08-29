"""Project structure checks (acceptance criteria B3/B4)."""

from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

# The eight domain apps from backend-ssd §4
DOMAIN_APPS = [
    "accounts",
    "catalog",
    "listings",
    "search",
    "subscriptions",
    "messaging",
    "moderation",
    "core",
]


def test_all_domain_apps_registered():
    """B3: all eight apps are registered in INSTALLED_APPS."""
    for app in DOMAIN_APPS:
        assert app in settings.INSTALLED_APPS, f"{app} is not registered in INSTALLED_APPS"


def test_rest_framework_registered():
    assert "rest_framework" in settings.INSTALLED_APPS


@pytest.mark.django_db
def test_no_migrations_pending():
    """makemigrations --check --dry-run must not detect uncommitted model changes."""
    out = StringIO()
    # With check=True the command exits with SystemExit(1) if migrations are missing
    call_command("makemigrations", check=True, dry_run=True, stdout=out)


def test_api_routes_are_versioned():
    """All domain API routes live under the /api/v1/ prefix.

    /api/cron/ is a deliberate, explicitly-requested exception: it's the path
    an external scheduler (Vercel Cron) calls directly, matching that
    ecosystem's `/api/cron/*` convention rather than this project's own
    versioning scheme.
    """
    from unibooks import urls

    unversioned_exceptions = {"api/cron/"}

    api_patterns = [
        p for p in urls.urlpatterns if str(p.pattern).startswith("api/")
    ]
    assert api_patterns, "No /api/ routes found; domain endpoints should be mounted"
    for pattern in api_patterns:
        path = str(pattern.pattern)
        if path in unversioned_exceptions:
            continue
        assert path.startswith("api/v1/"), (
            f"Unversioned API route: {pattern.pattern}"
        )

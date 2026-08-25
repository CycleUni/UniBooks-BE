"""Tests for the health/readiness endpoint (GET /api/v1/core/healthz/)."""

from unittest import mock
import pytest
from django.test import Client


@pytest.fixture
def api():
    return Client()


def test_healthz_healthy_case(api, db):
    """GET /api/v1/core/healthz/ returns 200 and healthy breakdown when all checks pass."""
    resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["checks"] == {
        "database": "ok",
        "cache": "ok",
        "migrations": "ok",
    }
    assert data["unapplied_migrations"] == 0


def test_healthz_unauthenticated_access(api, db):
    """Health check must be accessible without authentication headers."""
    resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 200


def test_healthz_database_failure(api, db):
    """When the database is unreachable, healthz returns 503 with database error."""
    with mock.patch("django.db.connection.cursor", side_effect=Exception("DB connection refused")):
        resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["database"] == "error"
    # Ensure error message/trace doesn't leak
    assert "DB connection refused" not in str(data)


def test_healthz_cache_failure(api, db):
    """When the cache fails, healthz returns 503 with cache error."""
    with mock.patch("django.core.cache.cache.set", side_effect=Exception("Redis connection timeout")):
        resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["cache"] == "error"
    assert "Redis connection timeout" not in str(data)


def test_healthz_unapplied_migrations(api, db):
    """When unapplied migrations exist, healthz returns 503 and reports the count."""
    fake_plan = [("accounts", "0002_migration"), ("catalog", "0003_migration")]
    with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan", return_value=fake_plan):
        resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["migrations"] == "error"
    assert data["unapplied_migrations"] == 2


def test_healthz_migration_executor_exception(api, db):
    """When migration plan determination fails with an exception, healthz returns 503 gracefully."""
    with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan", side_effect=Exception("Migration table corrupt")):
        resp = api.get("/api/v1/core/healthz/")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "unhealthy"
    assert data["checks"]["migrations"] == "error"
    assert data["unapplied_migrations"] is None
    assert "Migration table corrupt" not in str(data)

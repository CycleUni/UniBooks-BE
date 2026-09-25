"""Rate-limit tests for the accounts endpoints most exposed to abuse
(login brute-force, mass registration, verification-email spam), plus the
public book search endpoint that proxies to the Google Books API."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

from core.throttling import ScopedThrottle

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    # Throttle counters are rows now (rolled back per test); the cache is
    # still cleared so cached search results cannot leak between tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return Client()


def test_login_is_rate_limited_after_five_attempts(api, db):
    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(5):
        resp = api.post("/api/v1/auth/token/", payload, content_type="application/json")
        assert resp.status_code == 401

    resp = api.post("/api/v1/auth/token/", payload, content_type="application/json")
    assert resp.status_code == 429


def test_register_is_rate_limited_after_ten_attempts(api, db):
    def _register(i):
        return api.post(
            "/api/v1/auth/register/",
            {
                "email": f"throttle-user-{i}@example.com",
                "password": "some-strong-password-123",
                "first_name": "Throttle",
                "last_name": "User",
            },
            content_type="application/json",
        )

    for i in range(10):
        resp = _register(i)
        assert resp.status_code in (201, 400)

    resp = _register(10)
    assert resp.status_code == 429


def test_verification_request_is_rate_limited_after_three_attempts(api, db):
    from accounts.models import School
    from accounts.services import issue_tokens

    School.objects.create(region_id='TW', email_domain="school.edu.tw", name="Throttle Test School")

    user = User.objects.create_user(
        email="throttle-verify@example.com", first_name="Th", last_name="Rottle", password=PASSWORD
    )
    header = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}

    for _ in range(3):
        resp = api.post(
            "/api/v1/auth/verify/request/",
            {"edu_email": "throttle-verify@school.edu.tw"},
            content_type="application/json",
            **header,
        )
        assert resp.status_code == 200

    resp = api.post(
        "/api/v1/auth/verify/request/",
        {"edu_email": "throttle-verify@school.edu.tw"},
        content_type="application/json",
        **header,
    )
    assert resp.status_code == 429


def test_book_search_is_rate_limited_by_search_scope(api, db):
    # BookSearchView is public/unauthenticated and fans out to the Google
    # Books API, so it must enforce the 'search' throttle scope. Mock the
    # outbound Google Books call so the test doesn't hit the network, and
    # tighten the 'search' rate (ScopedThrottle.THROTTLE_RATES is a class
    # attribute snapshotted at import time, so override_settings alone does
    # not affect it -- patch it directly instead).
    with mock.patch.dict(ScopedThrottle.THROTTLE_RATES, {"search": "2/min"}), \
            mock.patch("search.views.search_google_books", return_value=[]):
        for _ in range(2):
            resp = api.get("/api/v1/search/books/?q=physics")
            assert resp.status_code == 200

        resp = api.get("/api/v1/search/books/?q=physics")
    assert resp.status_code == 429


# ---------------------------------------------------------------------
# Counters in Postgres (ADR 0001)
# ---------------------------------------------------------------------


def test_postgres_store_limits_login_after_five_attempts(api, db):
    from core.models import ThrottleCounter

    payload = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(5):
        assert api.post("/api/v1/auth/token/", payload, content_type="application/json").status_code == 401

    resp = api.post("/api/v1/auth/token/", payload, content_type="application/json")
    assert resp.status_code == 429
    assert 0 < int(resp["Retry-After"]) <= 60
    # Counted in the database, not the cache.
    assert ThrottleCounter.objects.get().count == 6


def test_postgres_store_keeps_counting_refused_requests(db):
    from core.throttling import PostgresThrottleStore

    store = PostgresThrottleStore()
    results = [store.hit("k", limit=2, window=60)[0] for _ in range(4)]
    assert results == [True, True, False, False]


def test_postgres_store_windows_are_independent(db):
    from core.throttling import PostgresThrottleStore

    store = PostgresThrottleStore()
    with mock.patch("core.throttling._now", return_value=1_000_000.0):
        assert store.hit("k", limit=1, window=60)[0] is True
        assert store.hit("k", limit=1, window=60)[0] is False
    with mock.patch("core.throttling._now", return_value=1_000_060.0):
        assert store.hit("k", limit=1, window=60)[0] is True


def test_postgres_store_purges_day_old_counters(db):
    from core.models import ThrottleCounter
    from core.throttling import PostgresThrottleStore, purge_throttle_counters

    store = PostgresThrottleStore()
    with mock.patch("core.throttling._now", return_value=1_000_000.0):
        store.hit("old", limit=5, window=60)
    store.hit("new", limit=5, window=60)

    assert purge_throttle_counters() == 1
    assert list(ThrottleCounter.objects.values_list("key", flat=True)) == ["new"]

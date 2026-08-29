"""Rate-limit tests for the accounts endpoints most exposed to abuse
(login brute-force, mass registration, verification-email spam), plus the
public book search endpoint that proxies to the Google Books API."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from rest_framework.throttling import ScopedRateThrottle

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    # ScopedRateThrottle counters live in CACHES["default"]; tests share a
    # process-wide LocMemCache, so each test needs a clean slate.
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
    # tighten the 'search' rate (ScopedRateThrottle.THROTTLE_RATES is a class
    # attribute snapshotted at import time, so override_settings alone does
    # not affect it -- patch it directly instead).
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"search": "2/min"}), \
            mock.patch("search.views.search_google_books", return_value=[]):
        for _ in range(2):
            resp = api.get("/api/v1/search/books/?q=physics")
            assert resp.status_code == 200

        resp = api.get("/api/v1/search/books/?q=physics")
    assert resp.status_code == 429

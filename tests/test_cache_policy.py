"""Cache invalidation (core/cache.py).

One rule: a write invalidates the caches it dirties, in every environment.
These pin that rule for both mechanisms — direct deletes for single known
keys, and generation bumps for key families whose names can't be enumerated.
"""

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import School
from accounts.services import issue_tokens
from catalog.models import Book
from core.cache import safe_cache_delete
from listings.models import Listing
from subscriptions.models import Subscription

from django.contrib.auth import get_user_model

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    u = User.objects.create_user(
        email="cachepolicy@test.edu.tw", first_name="Cache", last_name="Policy", password=PASSWORD
    )
    u.verified_at = timezone.now()
    u.save(update_fields=["verified_at"])
    return u


@pytest.fixture
def book(db):
    return Book.objects.create(isbn13="9786666666660", title="Cache Policy Book", source="manual")


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def seller(db):
    u = User.objects.create_user(
        email="cachepolicy-seller@test.edu.tw", first_name="Sell", last_name="Er", password=PASSWORD
    )
    u.verified_at = timezone.now()
    u.save(update_fields=["verified_at"])
    return u


# ---------------------------------------------------------------------
# safe_cache_delete: single known keys
# ---------------------------------------------------------------------


def test_safe_cache_delete_clears_the_key():
    cache.set("home_waitlist", ["stale"], timeout=300)

    safe_cache_delete("home_waitlist")

    assert cache.get("home_waitlist") is None


def test_safe_cache_delete_survives_a_cache_backend_error():
    """It runs inside model signals and request handlers — a Redis hiccup must
    not roll back the subscribe (or 500 an admin edit) that triggered it."""
    from unittest import mock

    with mock.patch("core.cache.cache.delete", side_effect=ConnectionError("redis down")):
        safe_cache_delete("home_waitlist")  # must not raise


# ---------------------------------------------------------------------
# Wired up end-to-end through the Subscription signal
# ---------------------------------------------------------------------


def test_subscription_write_clears_the_waitlist_cache(user, book):
    cache.set("home_waitlist", ["stale"], timeout=300)

    sub = Subscription.objects.create(user=user, book=book)
    assert cache.get("home_waitlist") is None

    cache.set("home_waitlist", ["stale"], timeout=300)
    sub.delete()
    assert cache.get("home_waitlist") is None


# ---------------------------------------------------------------------
# Admin-owned keys
# ---------------------------------------------------------------------


def test_admin_school_change_clears_cache(db):
    """`home_static_*` staleness is exactly the "I imported schools and the
    homepage still doesn't show them" report."""
    staff = User.objects.create_user(
        email="cachepolicy-staff@example.com", first_name="St", last_name="Aff",
        password=PASSWORD, is_staff=True,
    )
    header = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(staff)['access']}"}

    cache.set("home_static_en", {"schools": [], "categories": []}, timeout=86400)

    resp = Client().post(
        "/api/v1/admin/schools/",
        data='{"name": "Cache Policy University", "email_domain": "cachepolicy.edu.tw", "translations": {}}',
        content_type="application/json",
        **header,
    )

    assert resp.status_code == 201
    assert cache.get("home_static_en") is None


# ---------------------------------------------------------------------
# Generation counters: invalidating key families whose names can't be
# enumerated (page/school/language/limit are all baked into the key).
# ---------------------------------------------------------------------


def test_bumping_a_generation_changes_every_key_in_it():
    from core.cache import bump_cache_version, versioned_key

    before = versioned_key("listing_list", "en", "NTU", "", "3")
    bump_cache_version("listing_list")
    after = versioned_key("listing_list", "en", "NTU", "", "3")

    assert before != after


def test_book_detail_cache_key_varies_by_language():
    from core.cache import versioned_key

    key_en = versioned_key("book_detail", "en", "1", "9781234567890", "1")
    key_zh = versioned_key("book_detail", "zh-TW", "1", "9781234567890", "1")

    assert key_en != key_zh


def test_generation_never_goes_backwards_after_eviction():
    """A counter evicted from Redis must not restart low enough to re-issue a
    generation that older, still-unexpired entries were written under —
    that would resurrect stale data."""
    from core.cache import bump_cache_version, cache_version

    bump_cache_version("evict_probe")
    before = cache_version("evict_probe")

    cache.delete("cachever:evict_probe")  # simulate eviction

    assert cache_version("evict_probe") >= before


def test_new_listing_appears_in_a_cached_list_immediately(api, seller, book):
    assert api.get("/api/v1/listings/").json()["results"] == []

    Listing.objects.create(book=book, seller=seller, price=100, condition="new", status="active")

    results = api.get("/api/v1/listings/").json()["results"]
    assert len(results) == 1, "the list cache must not outlive the write that invalidated it"


def test_deleted_listing_disappears_from_the_book_page_immediately(api, seller, book):
    """The case that makes caching /book risky: a cached page keeps
    advertising an item that no longer exists, so buyers click through to a
    dead listing or message a seller about a book they already sold."""
    listing = Listing.objects.create(book=book, seller=seller, price=100, condition="new", status="active")

    resp = api.get(f"/api/v1/books/?isbn={book.isbn13}")
    assert resp.json()["listings"]["count"] == 1

    listing.delete()

    resp = api.get(f"/api/v1/books/?isbn={book.isbn13}")
    assert resp.json()["listings"]["count"] == 0


def test_sold_listing_is_not_served_from_a_stale_detail_cache(api, seller, book):
    listing = Listing.objects.create(book=book, seller=seller, price=100, condition="new", status="active")

    assert api.get(f"/api/v1/listings/{listing.id}/").json()["status"] == "active"

    listing.status = "sold"
    listing.save(update_fields=["status"])

    assert api.get(f"/api/v1/listings/{listing.id}/").json()["status"] == "sold"

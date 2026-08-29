"""Tests for the waitlist-notify cron endpoint (cron.views.WaitlistNotifyView),
triggered by an external scheduler (e.g. Vercel Cron) via a Bearer secret."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription

User = get_user_model()

CRON_SECRET = "test-only-cron-secret"


@pytest.fixture
def api():
    return Client()


@pytest.fixture(autouse=True)
def cron_secret(settings):
    settings.CRON_SECRET = CRON_SECRET


def cron_auth(token=CRON_SECRET):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


@pytest.fixture
def waitlister(db):
    return User.objects.create_user(email="waitlister@example.com", first_name="Wait", last_name="Lister", password="x")


@pytest.fixture
def seller(db):
    return User.objects.create_user(email="seller@example.com", first_name="Sell", last_name="Er", password="x")


@pytest.fixture
def book(db):
    return Book.objects.create(region_id='TW', isbn13="9781111111111", title="Waitlisted Book", source="manual")


def _subscribe_before_now(user, book_obj):
    sub = Subscription.objects.create(region_id='TW', user=user, book=book_obj)
    # created_at has auto_now_add — backdate it so a listing made "now" counts as new
    Subscription.objects.filter(id=sub.id).update(created_at=timezone.now() - timezone.timedelta(days=1))
    sub.refresh_from_db()
    return sub


def test_rejects_missing_auth_header(api, db):
    resp = api.get("/api/cron/waitlist-notify/")
    assert resp.status_code == 403


def test_rejects_wrong_token(api, db):
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth("wrong-token"))
    assert resp.status_code == 403


def test_rejects_when_cron_secret_unset(api, db, settings):
    settings.CRON_SECRET = ""
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 403


def test_noop_when_nothing_due(api, db):
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}


def test_notifies_user_with_new_listing_and_updates_notified_at(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1}
    assert len(mailoutbox) == 1
    assert waitlister.email in mailoutbox[0].to[0]
    assert "Waitlisted Book" in mailoutbox[0].body

    sub.refresh_from_db()
    assert sub.notified_at is not None


def test_does_not_renotify_already_notified_subscription(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    # Running again with no new listing since must not re-send
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 1


def test_renotifies_when_another_new_listing_appears_after_last_notification(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=150, condition='like_new', status='active')
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1}
    assert len(mailoutbox) == 2


def test_batches_multiple_due_subscriptions_for_same_user_into_one_email(api, waitlister, seller, book, mailoutbox):
    sub1 = _subscribe_before_now(waitlister, book)
    book2 = Book.objects.create(region_id='TW', isbn13="9782222222222", title="Second Waitlisted Book", source="manual")
    sub2 = _subscribe_before_now(waitlister, book2)

    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book2, seller=seller, price=200, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 2}
    assert len(mailoutbox) == 1
    assert "Waitlisted Book" in mailoutbox[0].body
    assert "Second Waitlisted Book" in mailoutbox[0].body


def test_ignores_non_active_listings(api, waitlister, seller, book, mailoutbox):
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='sold')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 0


def test_ignores_listings_created_before_subscription(api, waitlister, seller, book, mailoutbox):
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    # Subscribed after the listing already existed — nothing "new" for them
    Subscription.objects.create(region_id='TW', user=waitlister, book=book)

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0}
    assert len(mailoutbox) == 0


# ── CleanupView tests ────────────────────────────────────────────────────────


def test_cleanup_rejects_missing_auth_header(api, db):
    resp = api.get("/api/cron/cleanup/")
    assert resp.status_code == 403


def test_cleanup_rejects_wrong_token(api, db):
    resp = api.get("/api/cron/cleanup/", **cron_auth("wrong-token"))
    assert resp.status_code == 403


def test_cleanup_rejects_when_cron_secret_unset(api, db, settings):
    settings.CRON_SECRET = ""
    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 403


def test_cleanup_noop_when_no_orphan_books(api, db):
    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 0
    assert data["scanned_books"] == 0


def test_cleanup_deletes_orphan_books(api, db, seller):
    b1 = Book.objects.create(region_id='TW', isbn13="9780000000001", title="Orphan 1", source="manual")
    b2 = Book.objects.create(region_id='TW', isbn13="9780000000002", title="Orphan 2", source="manual")
    assert Book.objects.count() == 2

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 2
    assert data["scanned_books"] == 2
    assert Book.objects.count() == 0


def test_cleanup_keeps_books_with_listing(api, db, seller, book):
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new", status="active")

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 0
    assert data["scanned_books"] == 1
    assert Book.objects.filter(id=book.id).exists()


def test_cleanup_keeps_books_with_subscription(api, db, waitlister, book):
    Subscription.objects.create(region_id='TW', user=waitlister, book=book)

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 0
    assert data["scanned_books"] == 1
    assert Book.objects.filter(id=book.id).exists()


def test_cleanup_keeps_books_with_both_listing_and_subscription(api, db, seller, waitlister, book):
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=10, condition="new", status="active")
    Subscription.objects.create(region_id='TW', user=waitlister, book=book)

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 0
    assert data["scanned_books"] == 1
    assert Book.objects.filter(id=book.id).exists()


def test_cleanup_mixed_scenario(api, db, seller, waitlister):
    orphan = Book.objects.create(region_id='TW', isbn13="9780000000003", title="Orphan", source="manual")
    orphan2 = Book.objects.create(region_id='TW', isbn13="9780000000004", title="Orphan 2", source="manual")
    kept_with_listing = Book.objects.create(region_id='TW', isbn13="9780000000005", title="Has Listing",
                                           source="manual")
    kept_with_sub = Book.objects.create(region_id='TW', isbn13="9780000000006", title="Has Sub",
                                        source="manual")

    Listing.objects.create(region_id='TW', currency_id='TWD', book=kept_with_listing, seller=seller, price=10,
                          condition="new", status="active")
    Subscription.objects.create(region_id='TW', user=waitlister, book=kept_with_sub)

    assert Book.objects.count() == 4

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 2
    assert data["scanned_books"] == 4
    assert not Book.objects.filter(id=orphan.id).exists()
    assert not Book.objects.filter(id=orphan2.id).exists()
    assert Book.objects.filter(id=kept_with_listing.id).exists()
    assert Book.objects.filter(id=kept_with_sub.id).exists()


def test_cleanup_via_post(api, db):
    orphan = Book.objects.create(region_id='TW', isbn13="9780000000006", title="POST Orphan", source="manual")
    resp = api.post("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 1
    assert data["scanned_books"] == 1
    assert not Book.objects.filter(id=orphan.id).exists()


def test_cleanup_keeps_books_with_sold_or_removed_listings(api, db, seller, book):
    # Sold listing: book should still be kept (it's NOT orphan)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=10, condition="new", status="sold")

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["orphan_books_deleted"] == 0
    assert data["scanned_books"] == 1
    assert Book.objects.filter(id=book.id).exists()


def test_cleanup_deletes_preseed_book_when_orphan(api, db):
    book = Book.objects.create(region_id='TW', isbn13="9780000000007", title="Preseed Orphan",
                               source="preseed")
    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json()["orphan_books_deleted"] == 1
    assert not Book.objects.filter(id=book.id).exists()

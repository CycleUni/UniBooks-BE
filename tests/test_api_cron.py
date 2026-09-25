"""Tests for the waitlist-notify cron endpoint (cron.views.WaitlistNotifyView),
triggered by an external scheduler (e.g. Vercel Cron) via a Bearer secret."""

import pytest
from unittest import mock
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from catalog.models import Book
from listings.models import Listing
from orders.models import Order
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
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0, "remaining_users": 0}


def test_notifies_user_with_new_listing_and_updates_notified_at(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1, "remaining_users": 0}
    assert len(mailoutbox) == 1
    assert waitlister.email in mailoutbox[0].to[0]
    assert "Waitlisted Book" in mailoutbox[0].body
    # Region-prefixed, matching the frontend's /<region>/... route table: a
    # bare /book link is resolved against whichever region the reader last
    # used, not the one they subscribed in.
    assert f"/tw/book?isbn={book.isbn13}" in mailoutbox[0].body

    sub.refresh_from_db()
    assert sub.notified_at is not None


def test_waitlist_email_says_how_to_stop_it(api, waitlister, seller, book, mailoutbox):
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())

    body = mailoutbox[0].body
    assert body.count(f"{settings.FRONTEND_URL}/account/subscriptions") == 1
    assert "我的求書" in body


def test_waitlist_email_has_one_account_link_across_regions(api, waitlister, seller, book, mailoutbox):
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    hk_book = Book.objects.create(region_id='HK', isbn13="9782222222222", title="HK Waitlisted Book", source="manual")
    hk_sub = Subscription.objects.create(region_id='HK', user=waitlister, book=hk_book)
    Subscription.objects.filter(id=hk_sub.id).update(created_at=timezone.now() - timezone.timedelta(days=1))
    Listing.objects.create(region_id='HK', currency_id='HKD', book=hk_book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())

    assert len(mailoutbox) == 1
    body = mailoutbox[0].body
    # Each book keeps the region it was requested in...
    assert f"/tw/book?isbn={book.isbn13}" in body
    assert f"/hk/book?isbn={hk_book.isbn13}" in body
    # ...while the account page is linked once, without a region.
    assert body.count("/account/subscriptions") == 1
    assert f"{settings.FRONTEND_URL}/account/subscriptions" in body


@pytest.mark.parametrize("site_language, email_language, expect, absent", [
    # Nothing known about the user yet: the region's default (TW -> zh-TW).
    ("", "auto", "您求書清單中的以下書籍", "New listings are available"),
    # Follows the language they last used the site in.
    ("en", "auto", "New listings are available", "求書清單"),
    ("zh-HK", "auto", "你的求書清單中以下書籍", "New listings are available"),
    # An explicit choice beats the site language.
    ("zh-TW", "en", "New listings are available", "求書清單"),
])
def test_waitlist_email_is_written_in_one_language(api, waitlister, seller, book, mailoutbox, site_language, email_language, expect, absent):
    waitlister.site_language, waitlister.email_language = site_language, email_language
    waitlister.save(update_fields=["site_language", "email_language"])
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())

    mail = mailoutbox[0]
    assert expect in mail.body
    assert absent not in mail.body
    assert absent not in mail.subject
    assert "---" not in mail.body


def test_does_not_renotify_already_notified_subscription(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')

    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    # Running again with no new listing since must not re-send
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0, "remaining_users": 0}
    assert len(mailoutbox) == 1


def test_renotifies_when_another_new_listing_appears_after_last_notification(api, waitlister, seller, book, mailoutbox):
    sub = _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert len(mailoutbox) == 1

    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=150, condition='like_new', status='active')
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 1, "remaining_users": 0}
    assert len(mailoutbox) == 2


def test_batches_multiple_due_subscriptions_for_same_user_into_one_email(api, waitlister, seller, book, mailoutbox):
    sub1 = _subscribe_before_now(waitlister, book)
    book2 = Book.objects.create(region_id='TW', isbn13="9782222222222", title="Second Waitlisted Book", source="manual")
    sub2 = _subscribe_before_now(waitlister, book2)

    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book2, seller=seller, price=200, condition='new', status='active')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 1, "notified_subscriptions": 2, "remaining_users": 0}
    assert len(mailoutbox) == 1
    assert "Waitlisted Book" in mailoutbox[0].body
    assert "Second Waitlisted Book" in mailoutbox[0].body


def test_ignores_non_active_listings(api, waitlister, seller, book, mailoutbox):
    _subscribe_before_now(waitlister, book)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='sold')

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0, "remaining_users": 0}
    assert len(mailoutbox) == 0


def test_ignores_listings_created_before_subscription(api, waitlister, seller, book, mailoutbox):
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition='new', status='active')
    # Subscribed after the listing already existed — nothing "new" for them
    Subscription.objects.create(region_id='TW', user=waitlister, book=book)

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json() == {"notified_users": 0, "notified_subscriptions": 0, "remaining_users": 0}
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


def test_cleanup_purges_expired_refresh_tokens(api, db, seller):
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import RefreshTokenRecord
    from accounts.services import issue_tokens

    issue_tokens(seller)
    RefreshTokenRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    resp = api.get("/api/cron/cleanup/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json()["refresh_tokens_purged"] == 1
    assert not RefreshTokenRecord.objects.exists()


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


@pytest.mark.django_db
def test_notify_stops_at_the_per_run_cap_and_says_how_many_are_left(
    api, seller, book, settings, monkeypatch, mailoutbox
):
    """Sending is sequential inside a function Vercel kills at maxDuration, so
    a large waitlist used to be cut off mid-run. The cap makes the remainder
    the next run's work instead of a truncated one."""
    from cron import views as cron_views

    monkeypatch.setattr(cron_views, 'MAX_NOTIFY_USERS_PER_RUN', 1)
    Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller,
        price=100, condition='new', status='active',
    )
    for i in range(2):
        user = User.objects.create_user(
            email=f"waiting-{i}@example.com", first_name="W", last_name="L",
            password="test-only-password-123",
        )
        _subscribe_before_now(user, book)

    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["notified_users"] == 1
    assert body["remaining_users"] == 1
    assert len(mailoutbox) == 1

    # The next run picks up whoever is still due, with nothing lost.
    resp = api.get("/api/cron/waitlist-notify/", **cron_auth())
    assert resp.json()["notified_users"] == 1
    assert resp.json()["remaining_users"] == 0
    assert len(mailoutbox) == 2


# ── MeetupReminderView tests ──────────────────────────────────────────────────


def test_meetup_reminder_rejects_missing_auth_header(api, db):
    resp = api.get("/api/cron/meetup-reminder/")
    assert resp.status_code == 403


def test_meetup_reminder_rejects_wrong_token(api, db):
    resp = api.get("/api/cron/meetup-reminder/", **cron_auth("wrong-token"))
    assert resp.status_code == 403


def test_meetup_reminder_rejects_when_cron_secret_unset(api, db, settings):
    settings.CRON_SECRET = ""
    resp = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp.status_code == 403


def test_meetup_reminder_noop_when_no_orders(api, db):
    resp = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json() == {
        "reminded_orders": 0,
        "notified_users": 0,
        "remaining_orders": 0,
    }


def test_meetup_reminder_notifies_buyer_and_seller(api, db, seller, mailoutbox):
    buyer = User.objects.create_user(
        email="buyer@example.com", first_name="Buy", last_name="Er", password="x"
    )
    test_book = Book.objects.create(region_id='TW', isbn13="9782222222222", title="Algorithms Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=350, condition="good", status="reserved"
    )
    meetup_dt = timezone.now() + timezone.timedelta(minutes=30)
    order = Order.objects.create(
        region_id='TW',
        currency_id='TWD',
        buyer=buyer,
        seller=seller,
        listing=test_listing,
        total_amount=test_listing.price,
        status='accepted',
        meetup_time=meetup_dt,
        meetup_location="Main Library Gate",
    )

    resp = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["reminded_orders"] == 1
    assert data["notified_users"] == 2
    assert data["remaining_orders"] == 0

    assert len(mailoutbox) == 2
    recipients = {m.to[0] for m in mailoutbox}
    assert recipients == {"buyer@example.com", "seller@example.com"}

    for mail in mailoutbox:
        assert "Algorithms Book" in mail.subject
        assert "Main Library Gate" in mail.body
        assert "/account/orders" in mail.body

    order.refresh_from_db()
    assert order.meetup_reminder_sent_at is not None

    # Re-running immediately does not re-notify (deduplication)
    mailoutbox.clear()
    resp2 = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp2.status_code == 200
    assert resp2.json()["reminded_orders"] == 0
    assert len(mailoutbox) == 0


def test_meetup_reminder_ignores_past_and_distant_meetups(api, db, seller, mailoutbox):
    buyer = User.objects.create_user(email="buyer2@example.com", first_name="Buy2", last_name="Er2", password="x")
    test_book = Book.objects.create(region_id='TW', title="Far Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=100, status="reserved"
    )
    # Past meetup
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='accepted',
        meetup_time=timezone.now() - timezone.timedelta(minutes=15)
    )
    # More than 1 hour away
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='accepted',
        meetup_time=timezone.now() + timezone.timedelta(hours=2)
    )

    resp = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json()["reminded_orders"] == 0
    assert len(mailoutbox) == 0


def test_meetup_reminder_overlaps_hourly_runs_so_a_late_run_misses_nothing(api, db, seller, mailoutbox):
    """Hourly runs with an exact one-hour lookahead leave a gap whenever a run
    fires later than the previous one: a meetup 62 minutes out is past the
    window of a run at :00 and already past by a run at :02+60. The 65-minute
    lookahead catches it on the earlier run, and only once."""
    buyer = User.objects.create_user(email="edge@example.com", first_name="Ed", last_name="Ge", password="x")
    book = Book.objects.create(region_id='TW', title="Edge Book", source="manual")
    listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="good", status="reserved"
    )
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        total_amount=100, status='accepted', meetup_time=timezone.now() + timezone.timedelta(minutes=62),
    )

    first = api.get("/api/cron/meetup-reminder/", **cron_auth())
    second = api.get("/api/cron/meetup-reminder/", **cron_auth())

    assert first.json()["reminded_orders"] == 1
    assert second.json()["reminded_orders"] == 0
    assert len(mailoutbox) == 2


def test_meetup_reminder_ignores_non_accepted_statuses(api, db, seller, mailoutbox):
    buyer = User.objects.create_user(email="buyer3@example.com", first_name="Buy3", last_name="Er3", password="x")
    test_book = Book.objects.create(region_id='TW', title="Pending Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=100, status="active"
    )
    due_time = timezone.now() + timezone.timedelta(minutes=20)
    # Pending order
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='pending',
        meetup_time=due_time
    )
    # Completed order
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='completed',
        meetup_time=due_time
    )
    # Cancelled order
    Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='cancelled',
        meetup_time=due_time
    )

    resp = api.get("/api/cron/meetup-reminder/", **cron_auth())
    assert resp.status_code == 200
    assert resp.json()["reminded_orders"] == 0
    assert len(mailoutbox) == 0


def test_meetup_rescheduling_clears_reminder_sent_at(api, db, seller):
    from accounts.services import issue_tokens
    from accounts.models import RegionVerification
    from messaging.models import Conversation

    buyer = User.objects.create_user(email="buyer4@example.com", first_name="Buy4", last_name="Er4", password="x")
    for u in (buyer, seller):
        RegionVerification.objects.update_or_create(
            user=u, region_id='TW', defaults={'edu_email': u.email, 'verified_at': timezone.now()}
        )
    test_book = Book.objects.create(region_id='TW', title="Reschedule Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=100, status="reserved"
    )
    Conversation.objects.get_or_create(listing=test_listing, buyer=buyer)
    meetup_dt = timezone.now() + timezone.timedelta(minutes=30)
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
        listing=test_listing, total_amount=100, status='accepted',
        meetup_time=meetup_dt, meetup_reminder_sent_at=timezone.now()
    )

    new_time = (timezone.now() + timezone.timedelta(hours=3)).isoformat()
    seller_header = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(seller)['access']}"}
    resp = api.patch(
        f"/api/v1/orders/{order.id}/",
        {"meetup_time": new_time},
        content_type="application/json",
        **seller_header,
    )
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.meetup_reminder_sent_at is None
    assert order.buyer_reminder_sent_at is None
    assert order.seller_reminder_sent_at is None


def test_meetup_reminder_send_mail_failure_does_not_mark_order(api, db, seller, mailoutbox):
    buyer = User.objects.create_user(
        email="buyer_fail@example.com", first_name="BuyFail", last_name="Er", password="x"
    )
    test_book = Book.objects.create(region_id='TW', title="Fail Email Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=200, status="reserved"
    )
    meetup_dt = timezone.now() + timezone.timedelta(minutes=30)
    order = Order.objects.create(
        region_id='TW',
        currency_id='TWD',
        buyer=buyer,
        seller=seller,
        listing=test_listing,
        total_amount=200,
        status='accepted',
        meetup_time=meetup_dt,
        meetup_location="Gate 1",
    )

    # When send_mail raises an exception, the reminder must NOT be marked as sent
    with mock.patch('cron.views.send_mail', side_effect=Exception('Mail server unavailable')):
        resp = api.get('/api/cron/meetup-reminder/', **cron_auth())
        assert resp.status_code == 200
        assert resp.json()['reminded_orders'] == 0
        assert resp.json()['notified_users'] == 0

    order.refresh_from_db()
    assert order.meetup_reminder_sent_at is None

    # Subsequent run when mail works: order is reminded and marked sent
    resp2 = api.get('/api/cron/meetup-reminder/', **cron_auth())
    assert resp2.status_code == 200
    assert resp2.json()['reminded_orders'] == 1
    assert resp2.json()['notified_users'] == 2

    order.refresh_from_db()
    assert order.meetup_reminder_sent_at is not None


def test_waitlist_notify_send_mail_failure_does_not_mark_subscription(api, db, waitlister, seller, book, mailoutbox):
    sub = Subscription.objects.create(region_id='TW', user=waitlister, book=book)
    Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller,
        price=100, condition='new', status='active'
    )

    # When send_mail raises an exception, the subscription must NOT be marked as notified
    with mock.patch('cron.views.send_mail', side_effect=Exception('Mail server down')):
        resp = api.get('/api/cron/waitlist-notify/', **cron_auth())
        assert resp.status_code == 200
        assert resp.json()['notified_users'] == 0
        assert resp.json()['notified_subscriptions'] == 0

    sub.refresh_from_db()
    assert sub.notified_at is None

    # Subsequent run when mail works: subscription is notified
    resp2 = api.get('/api/cron/waitlist-notify/', **cron_auth())
    assert resp2.status_code == 200
    assert resp2.json()['notified_users'] == 1
    assert resp2.json()['notified_subscriptions'] == 1

    sub.refresh_from_db()
    assert sub.notified_at is not None


def test_meetup_reminder_partial_failure_does_not_double_send_to_succeeded_party(api, db, seller, mailoutbox):
    buyer = User.objects.create_user(
        email="buyer_partial@example.com", first_name="BuyPart", last_name="Er", password="x"
    )
    test_book = Book.objects.create(region_id='TW', title="Partial Fail Book", source="manual")
    test_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=test_book, seller=seller,
        price=150, status="reserved"
    )
    meetup_dt = timezone.now() + timezone.timedelta(minutes=45)
    order = Order.objects.create(
        region_id='TW',
        currency_id='TWD',
        buyer=buyer,
        seller=seller,
        listing=test_listing,
        total_amount=150,
        status='accepted',
        meetup_time=meetup_dt,
        meetup_location="Station Exit A",
    )

    from django.core.mail import send_mail as real_send_mail
    def mock_send_mail(*args, **kwargs):
        if kwargs.get('recipient_list') == [seller.email]:
            raise Exception("Seller SMTP error")
        return real_send_mail(*args, **kwargs)

    # First run: buyer succeeds, seller fails
    with mock.patch('cron.views.send_mail', side_effect=mock_send_mail):
        resp = api.get('/api/cron/meetup-reminder/', **cron_auth())
        assert resp.status_code == 200
        assert resp.json()['reminded_orders'] == 0
        assert resp.json()['notified_users'] == 1

    order.refresh_from_db()
    assert order.buyer_reminder_sent_at is not None
    assert order.seller_reminder_sent_at is None
    assert order.meetup_reminder_sent_at is None
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [buyer.email]

    # Reset outbox to inspect second run
    mailoutbox.clear()

    # Second run: mail works now. Only seller should receive email; buyer must NOT be double-sent.
    resp2 = api.get('/api/cron/meetup-reminder/', **cron_auth())
    assert resp2.status_code == 200
    assert resp2.json()['reminded_orders'] == 1
    assert resp2.json()['notified_users'] == 1

    order.refresh_from_db()
    assert order.buyer_reminder_sent_at is not None
    assert order.seller_reminder_sent_at is not None
    assert order.meetup_reminder_sent_at is not None
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [seller.email]

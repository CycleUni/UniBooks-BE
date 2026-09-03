"""Regression tests for the authorization / data-exposure fixes made in the
2026-09 security review. Each test names the bug it pins.

Red line: every credential below is a fictitious test fake.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import RegionVerification, School
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from moderation.models import ChatReport, Report
from orders.models import Order, Review

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return Client()


def _verified_user(email, region='TW'):
    u = User.objects.create_user(email=email, first_name="Te", last_name="St", password=PASSWORD)
    RegionVerification.objects.update_or_create(
        user=u, region_id=region, defaults={'edu_email': email, 'verified_at': timezone.now()}
    )
    return u


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def seller(db):
    return _verified_user("sec-seller@example.com")


@pytest.fixture
def buyer(db):
    return _verified_user("sec-buyer@example.com")


@pytest.fixture
def outsider(db):
    return _verified_user("sec-outsider@example.com")


@pytest.fixture
def book(db):
    return Book.objects.create(region_id='TW', isbn13="9789999999991", title="Sec Book", source="manual")


@pytest.fixture
def listing(db, seller, book):
    return Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new",
        private_note="bought at 50, do not tell buyers",
    )


# ---------------------------------------------------------------------
# private_note must never reach a shared cache
# ---------------------------------------------------------------------


def test_listing_detail_cache_does_not_leak_private_note(api, listing, seller, buyer):
    # The seller looks first and sees their note (this is what used to seed
    # the shared cache with it).
    own = api.get(f"/api/v1/listings/{listing.id}/", **bearer(seller)).json()
    assert own["private_note"] == "bought at 50, do not tell buyers"

    anonymous = api.get(f"/api/v1/listings/{listing.id}/").json()
    assert "private_note" not in anonymous

    other = api.get(f"/api/v1/listings/{listing.id}/", **bearer(buyer)).json()
    assert "private_note" not in other

    # And again as the seller: the note comes back even though the cached
    # body does not carry it.
    assert api.get(f"/api/v1/listings/{listing.id}/", **bearer(seller)).json()["private_note"]


def test_listing_list_cache_does_not_leak_private_note(api, listing, seller, buyer):
    api.get(f"/api/v1/listings/?seller_id={seller.id}", **bearer(seller))
    rows = api.get(f"/api/v1/listings/?seller_id={seller.id}", **bearer(buyer)).json()["results"]
    assert rows and all("private_note" not in row for row in rows)


def test_book_detail_cache_does_not_leak_private_note(api, listing, book, seller, buyer):
    api.get(f"/api/v1/books/?isbn={book.isbn13}", **bearer(seller))
    rows = api.get(f"/api/v1/books/?isbn={book.isbn13}", **bearer(buyer)).json()["listings"]["results"]
    assert rows and all("private_note" not in row for row in rows)


# ---------------------------------------------------------------------
# Changing the login email must not open a path to unearned verification
# ---------------------------------------------------------------------


def test_profile_email_change_rejects_campus_subdomain(api, db):
    School.objects.create(email_domain="ntu.edu.tw", name="NTU", region_id='TW')
    user = User.objects.create_user(email="plain@gmail.com", first_name="P", last_name="L", password=PASSWORD)

    # `mail.ntu.edu.tw` is not a School row, but resolve_school_from_email
    # walks up the domain and accepts it — so the exact-domain check that
    # used to live here let it through, and /verify/auto/ then granted a
    # student verification for an address never proven.
    resp = api.patch(
        "/api/v1/auth/me/", {"email": "me@mail.ntu.edu.tw"}, content_type="application/json", **bearer(user)
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "acct.errEduEmailChangeNotAllowed"
    user.refresh_from_db()
    assert user.email == "plain@gmail.com"

    verify = api.post("/api/v1/auth/verify/auto/", **bearer(user))
    assert verify.status_code == 400
    assert not user.region_verifications.verified().exists()


# ---------------------------------------------------------------------
# Google sign-in must trust only verified addresses
# ---------------------------------------------------------------------


def _google_login(api, idinfo):
    fake_adapter = mock.MagicMock()
    fake_adapter.get_provider.return_value.app = SimpleNamespace(client_id="fake-client-id")
    with mock.patch("allauth.socialaccount.adapter.get_adapter", return_value=fake_adapter), \
         mock.patch("allauth.socialaccount.providers.google.views._verify_and_decode", return_value=idinfo):
        return api.post("/api/v1/auth/google/", {"credential": "fake-id-token"}, content_type="application/json")


def test_google_login_rejects_unverified_email(api, db):
    victim = User.objects.create_user(email="victim@example.com", first_name="V", last_name="I", password=PASSWORD)
    resp = _google_login(api, {"sub": "g-1", "email": "victim@example.com", "email_verified": False})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.errEmailNotVerified"
    assert not victim.socialaccount_set.exists()


def test_google_login_accepts_verified_email_and_normalises_case(api, db):
    resp = _google_login(api, {"sub": "g-2", "email": "New.Person@Example.com", "email_verified": True,
                               "given_name": "New", "family_name": "Person"})
    assert resp.status_code == 200
    assert "access" in resp.json()
    user = User.objects.get(email="new.person@example.com")
    assert not user.has_usable_password()

    # A second sign-in with a different case must land on the same account.
    again = _google_login(api, {"sub": "g-2", "email": "NEW.PERSON@example.com", "email_verified": True})
    assert again.status_code == 200
    assert User.objects.filter(email__iexact="new.person@example.com").count() == 1


# ---------------------------------------------------------------------
# Reviews are write-once; orders cannot be deleted
# ---------------------------------------------------------------------


def test_reviewee_cannot_edit_or_delete_review(api, buyer, seller, listing):
    order = Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
                                 listing=listing, total_amount=100, status="completed")
    review = Review.objects.create(order=order, reviewer=buyer, reviewee=seller, rating=1, comment="bad")

    for who in (seller, buyer):
        assert api.patch(f"/api/v1/orders/reviews/{review.id}/", {"rating": 5},
                         content_type="application/json", **bearer(who)).status_code == 405
        assert api.delete(f"/api/v1/orders/reviews/{review.id}/", **bearer(who)).status_code == 405
    review.refresh_from_db()
    assert review.rating == 1


def test_order_cannot_be_deleted_by_either_party(api, buyer, seller, listing):
    order = Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
                                 listing=listing, total_amount=100, status="completed")
    for who in (seller, buyer):
        assert api.delete(f"/api/v1/orders/{order.id}/", **bearer(who)).status_code == 405
    assert Order.objects.filter(id=order.id).exists()


@pytest.mark.parametrize("rating", [0, 6, None])
def test_review_rating_must_be_one_to_five(api, buyer, seller, listing, rating):
    order = Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
                                 listing=listing, total_amount=100, status="completed")
    resp = api.post("/api/v1/orders/reviews/", {"order": str(order.id), "rating": rating, "comment": "x"},
                    content_type="application/json", **bearer(buyer))
    assert resp.status_code == 400
    assert "rating" in resp.json()


# ---------------------------------------------------------------------
# Order state machine vs. the listing it reserves
# ---------------------------------------------------------------------


def test_accept_refused_when_listing_already_reserved(api, buyer, seller, listing, outsider):
    Conversation.objects.create(listing=listing, buyer=buyer)
    Conversation.objects.create(listing=listing, buyer=outsider)
    first = Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
                                 listing=listing, total_amount=100)
    second = Order.objects.create(region_id='TW', currency_id='TWD', buyer=outsider, seller=seller,
                                  listing=listing, total_amount=100)

    assert api.patch(f"/api/v1/orders/{first.id}/", {"status": "accepted"},
                     content_type="application/json", **bearer(seller)).status_code == 200
    resp = api.patch(f"/api/v1/orders/{second.id}/", {"status": "accepted"},
                     content_type="application/json", **bearer(seller))
    assert resp.status_code == 400
    second.refresh_from_db()
    assert second.status == "pending"
    listing.refresh_from_db()
    assert listing.status == "reserved"


def test_cancelling_pending_order_does_not_reactivate_sold_listing(api, buyer, seller, listing):
    Conversation.objects.create(listing=listing, buyer=buyer)
    order = Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller,
                                 listing=listing, total_amount=100)
    listing.status = "sold"
    listing.save(update_fields=["status"])

    assert api.patch(f"/api/v1/orders/{order.id}/", {"status": "cancelled"},
                     content_type="application/json", **bearer(buyer)).status_code == 200
    listing.refresh_from_db()
    assert listing.status == "sold"


# ---------------------------------------------------------------------
# Chat reports name the real counterpart; staff moderation is region-scoped
# ---------------------------------------------------------------------


def test_chat_report_reported_party_must_be_the_other_participant(api, buyer, seller, listing, outsider):
    conv = Conversation.objects.create(listing=listing, buyer=buyer)
    resp = api.post("/api/v1/moderation/chat-reports/",
                    {"conversation": str(conv.id), "reported_party": outsider.id, "reason": "spam"},
                    content_type="application/json", **bearer(buyer))
    assert resp.status_code == 400
    assert "reported_party" in resp.json()
    assert not ChatReport.objects.exists()

    ok = api.post("/api/v1/moderation/chat-reports/",
                  {"conversation": str(conv.id), "reported_party": seller.id, "reason": "spam"},
                  content_type="application/json", **bearer(buyer))
    assert ok.status_code == 201


def test_staff_moderation_lists_are_scoped_to_managed_regions(api, buyer, listing, db):
    report = Report.objects.create(reporter=buyer, listing=listing, reason="scam")
    hk_manager = User.objects.create_user(email="hk-mgr@example.com", first_name="H", last_name="K",
                                          password=PASSWORD, is_staff=True)
    hk_manager.managed_regions.add('HK')

    listed = api.get("/api/v1/moderation/all/", **bearer(hk_manager)).json()["results"]
    assert listed == []
    assert api.patch(f"/api/v1/moderation/{report.id}/", {"status": "dismissed"},
                     content_type="application/json", **bearer(hk_manager)).status_code == 404

    superuser = User.objects.create_superuser(email="root@example.com", first_name="R", last_name="T", password=PASSWORD)
    assert len(api.get("/api/v1/moderation/all/", **bearer(superuser)).json()["results"]) == 1


# ---------------------------------------------------------------------
# Course facet cache is per region
# ---------------------------------------------------------------------


def test_course_list_is_not_shared_across_regions(api, listing):
    listing.course_name = "Calculus I"
    listing.save(update_fields=["course_name"])

    tw = api.get("/api/v1/search/courses/?region=TW").json()
    assert [c["value"] for c in tw] == ["Calculus I"]
    hk = api.get("/api/v1/search/courses/?region=HK").json()
    assert hk == []


# ---------------------------------------------------------------------
# Malformed ids answer 4xx, not 500
# ---------------------------------------------------------------------


def test_malformed_uuids_do_not_500(api, buyer):
    assert api.post("/api/v1/orders/", {"listing": "not-a-uuid"},
                    content_type="application/json", **bearer(buyer)).status_code == 400
    assert api.post("/api/v1/messaging/conversations/", {"listing_id": "not-a-uuid"},
                    content_type="application/json", **bearer(buyer)).status_code == 404
    assert api.post("/api/v1/moderation/chat-reports/",
                    {"conversation": "not-a-uuid", "reported_party": buyer.id, "reason": "spam"},
                    content_type="application/json", **bearer(buyer)).status_code == 400


def test_change_password_runs_validators(api, buyer):
    resp = api.post("/api/v1/auth/password/", {"old_password": PASSWORD, "new_password": "1"},
                    content_type="application/json", **bearer(buyer))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errValidation"
    buyer.refresh_from_db()
    assert buyer.check_password(PASSWORD)

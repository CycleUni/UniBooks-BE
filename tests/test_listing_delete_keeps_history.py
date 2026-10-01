"""A seller deleting a listing keeps its orders, chat and reports (listings/snapshot.py)."""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from rest_framework_simplejwt.backends import TokenBackend

from accounts.models import RegionVerification
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from moderation.models import Report
from orders.models import Order, Review

User = get_user_model()


def make_user(email, **extra):
    user = User.objects.create_user(email=email, first_name=email.split("@")[0], last_name="Test", password="test-only-password-123", **extra)
    RegionVerification.objects.create(user=user, region_id='TW', edu_email=email, verified_at=timezone.now())
    return user


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def api():
    return Client()


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def seller(db):
    return make_user("keep-seller@test.edu.tw")


@pytest.fixture
def buyer(db):
    return make_user("keep-buyer@test.edu.tw")


@pytest.fixture
def listing(db, seller):
    book = Book.objects.create(region_id='TW', isbn13="9786666666666", title="Kept Book", source="manual")
    return Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=150, condition="new", status="sold")


@pytest.fixture
def history(db, listing, seller, buyer):
    """A finished sale: the chat, the completed order, a review and a report."""
    conversation = Conversation.objects.create(listing=listing, buyer=buyer, latest_message_body="hi")
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        status="completed", total_amount=150, completed_at=timezone.now(),
    )
    review = Review.objects.create(order=order, reviewer=buyer, reviewee=seller, rating=5)
    report = Report.objects.create(reporter=buyer, listing=listing, reason="fake")
    return conversation, order, review, report


def test_rows_snapshot_the_listing_when_created(history, listing, seller):
    conversation, order, _, report = history
    for row in (conversation, order, report):
        assert row.listing_ref == listing.id
        assert row.book_title == "Kept Book"
    assert order.book_isbn == conversation.book_isbn == "9786666666666"
    assert conversation.seller_id == report.seller_id == seller.id
    assert conversation.region_id == report.region_id == 'TW'


def test_seller_deletes_listing_and_history_stays(api, history, listing, seller, buyer):
    conversation, order, review, report = history
    resp = api.delete(f"/api/v1/listings/{listing.id}/", **bearer(seller))
    assert resp.status_code == 204
    assert not Listing.objects.filter(id=listing.id).exists()

    for row in (conversation, order, review, report):
        assert type(row).objects.filter(pk=row.pk).exists()
    order.refresh_from_db()
    assert order.listing_id is None and order.listing_ref == listing.id

    # The buyer's order history still says what was bought.
    rows = api.get("/api/v1/orders/", **bearer(buyer)).json()
    rows = rows.get("results", rows)
    mine = next(o for o in rows if o["id"] == str(order.id))
    assert mine["listing_title"] == "Kept Book"
    assert mine["listing_deleted"] is True
    assert mine["conversation_id"] == str(conversation.id)

    # Both inboxes still hold the chat, and it still finds its order.
    for user, other_role in ((buyer, "seller"), (seller, "buyer")):
        chats = api.get("/api/v1/messaging/conversations/", **bearer(user)).json()
        chats = chats.get("results", chats)
        chat = next(c for c in chats if c["id"] == str(conversation.id))
        assert chat["listing_deleted"] is True
        assert chat["listing_title"] == "Kept Book"
        assert chat["listing_id"] == str(listing.id)
        assert chat["listing_photo"] == ""
        assert chat["order_id"] == str(order.id)
        assert chat["other_party_role"] == other_role


def test_seller_cannot_delete_listing_with_an_open_order(api, listing, seller, buyer):
    Listing.objects.filter(pk=listing.pk).update(status="reserved")
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        status="accepted", total_amount=150,
    )
    resp = api.delete(f"/api/v1/listings/{listing.id}/", **bearer(seller))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "listing.errHasActiveOrders"
    assert Listing.objects.filter(id=listing.id).exists()
    order.refresh_from_db()
    assert order.status == "accepted"


def test_chat_of_a_deleted_listing_is_read_only(api, settings, history, listing, seller, buyer):
    settings.EDGE_CHAT_JWT_SECRET = "test-only-edge-chat-secret"
    conversation = history[0]
    backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)

    def claims():
        resp = api.get(f"/api/v1/messaging/chat-token/?conversation_id={conversation.id}", **bearer(buyer))
        assert resp.status_code == 200
        return backend.decode(resp.json()["token"], verify=True)

    assert "role" not in claims()
    api.delete(f"/api/v1/listings/{listing.id}/", **bearer(seller))
    after = claims()
    assert after["role"] == "observer"
    assert set(after["participant_ids"]) == {str(buyer.id), str(seller.id)}

    resp = api.post(
        "/api/v1/messaging/uploads/",
        {"conversation_id": str(conversation.id), "content_type": "image/png"},
        content_type="application/json",
        **bearer(buyer),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "msg.errListingDeleted"


def test_staff_still_see_the_report_after_the_listing_is_gone(api, history, listing, seller):
    report = history[3]
    staff = make_user("keep-staff@test.edu.tw", is_staff=True)
    staff.managed_regions.add('TW')
    api.delete(f"/api/v1/listings/{listing.id}/", **bearer(seller))

    rows = api.get("/api/v1/moderation/all/", **bearer(staff)).json()
    rows = rows.get("results", rows)
    row = next(r for r in rows if r["id"] == str(report.id))
    assert row["listing"] == {"id": str(listing.id), "title": "Kept Book", "deleted": True}


def test_actioning_a_report_on_a_deleted_listing_does_not_fail(api, history, listing, seller):
    report = history[3]
    staff = make_user("keep-staff2@test.edu.tw", is_staff=True)
    staff.managed_regions.add('TW')
    api.delete(f"/api/v1/listings/{listing.id}/", **bearer(seller))
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/", {"status": "actioned"},
        content_type="application/json", **bearer(staff),
    )
    assert resp.status_code == 200
    report.refresh_from_db()
    assert report.status == "actioned"

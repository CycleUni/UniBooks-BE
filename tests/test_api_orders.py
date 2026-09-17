"""API tests for orders.views: OrderViewSet transition authorization and the
ReviewViewSet.perform_create validation-error crash fix (NameError from a
missing `serializers` import)."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from orders.models import Order, Review

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def buyer(db):
    u = User.objects.create_user(email="buyer@example.com", first_name="Bu", last_name="Yer", password=PASSWORD)
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


@pytest.fixture
def seller(db):
    u = User.objects.create_user(email="seller@example.com", first_name="Se", last_name="Ller", password=PASSWORD)
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


def _auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def buyer_header(buyer):
    return _auth_header(buyer)


@pytest.fixture
def seller_header(seller):
    return _auth_header(seller)


@pytest.fixture
def listing(db, seller):
    book = Book.objects.create(region_id='TW', title="Orders Test Book", source="manual")
    return Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new")


@pytest.fixture
def order(db, buyer, seller, listing, status="pending"):
    Conversation.objects.get_or_create(listing=listing, buyer=buyer)
    return Order.objects.create(region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing, total_amount=listing.price, status=status)


def _patch_status(api, order, new_status, header):
    return api.patch(
        f"/api/v1/orders/{order.id}/",
        {"status": new_status},
        content_type="application/json",
        **header,
    )


# ---------------------------------------------------------------------
# Fix 2: role-based transition authorization
# ---------------------------------------------------------------------


def test_buyer_cannot_self_approve_pending_order(api, order, buyer_header):
    resp = _patch_status(api, order, "accepted", buyer_header)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "order.errSellerOnly"
    order.refresh_from_db()
    assert order.status == "pending"


def test_seller_can_approve_pending_order(api, order, seller_header):
    resp = _patch_status(api, order, "accepted", seller_header)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == "accepted"


def test_seller_cannot_mark_completed_directly(api, order, seller_header, buyer_header):
    # Seller first accepts, then hands over; only the buyer may mark "completed".
    _patch_status(api, order, "accepted", seller_header)
    _patch_status(api, order, "handed_over", seller_header)
    resp = _patch_status(api, order, "completed", seller_header)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "order.errBuyerOnly"


def test_buyer_cannot_mark_handed_over(api, order, seller_header, buyer_header):
    _patch_status(api, order, "accepted", seller_header)
    resp = _patch_status(api, order, "handed_over", buyer_header)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "order.errSellerOnly"


def test_buyer_confirms_completion_after_handover(api, order, seller_header, buyer_header):
    _patch_status(api, order, "accepted", seller_header)
    _patch_status(api, order, "handed_over", seller_header)
    assert Order.objects.get(pk=order.pk).completed_at is None
    resp = _patch_status(api, order, "completed", buyer_header)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == "completed"
    assert order.completed_at is not None  # the completion time statistics rely on


def test_either_party_can_cancel_pending_order(api, order, buyer_header):
    resp = _patch_status(api, order, "cancelled", buyer_header)
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == "cancelled"


def test_outsider_cannot_touch_order_status(api, order, db):
    outsider = User.objects.create_user(
        email="order-outsider@example.com", first_name="Out", last_name="Sider", password=PASSWORD
    )
    resp = _patch_status(api, order, "accepted", _auth_header(outsider))
    # Outsider is excluded from get_queryset entirely -> 404, not 403.
    assert resp.status_code == 404


# ---------------------------------------------------------------------
# Fix 1: ReviewViewSet.perform_create validation errors must not crash
# ---------------------------------------------------------------------


def test_duplicate_review_returns_clean_400_not_500(api, buyer, seller, listing, buyer_header):
    completed_order = Order.objects.create(region_id='TW', currency_id='TWD', 
        buyer=buyer, seller=seller, listing=listing, total_amount=listing.price, status="completed"
    )
    Review.objects.create(order=completed_order, reviewer=buyer, reviewee=seller, rating=5)

    resp = api.post(
        "/api/v1/orders/reviews/",
        {"order": str(completed_order.id), "rating": 4, "comment": "again"},
        content_type="application/json",
        **buyer_header,
    )
    assert resp.status_code == 400
    assert "already reviewed" in str(resp.json())


def test_review_of_others_order_returns_clean_400_not_500(api, buyer, seller, listing, db):
    completed_order = Order.objects.create(region_id='TW', currency_id='TWD', 
        buyer=buyer, seller=seller, listing=listing, total_amount=listing.price, status="completed"
    )
    outsider = User.objects.create_user(
        email="review-outsider@example.com", first_name="Out", last_name="Sider", password=PASSWORD
    )
    from accounts.models import RegionVerification
    RegionVerification.objects.create(user=outsider, region_id='TW', edu_email='out@ntu.edu.tw', is_active=True, is_manual_verification=True, verified_at=timezone.now())
    resp = api.post(
        "/api/v1/orders/reviews/",
        {"order": str(completed_order.id), "rating": 4, "comment": "hi"},
        content_type="application/json",
        **_auth_header(outsider),
    )
    assert resp.status_code == 400
    assert "own orders" in str(resp.json())


def test_review_of_non_completed_order_returns_clean_400_not_500(api, order, buyer_header):
    # `order` fixture defaults to "pending" status.
    resp = api.post(
        "/api/v1/orders/reviews/",
        {"order": str(order.id), "rating": 5, "comment": "too early"},
        content_type="application/json",
        **buyer_header,
    )
    assert resp.status_code == 400
    assert "completed" in str(resp.json())


# ---------------------------------------------------------------------
# Inbox preview follows the chat room, not the attempt to post into it
# ---------------------------------------------------------------------


@pytest.mark.parametrize("posted", [True, False])
def test_order_notification_previews_only_what_reached_the_chat_room(api, order, seller_header, monkeypatch, posted):
    # The opened conversation is rendered from CFEdgeChat's history. Writing
    # the preview before (and regardless of) the post left the inbox saying
    # "Seller rejected the meetup" over a conversation with no such message.
    import orders.views.orders as order_views

    conv = Conversation.objects.get(listing=order.listing, buyer=order.buyer)
    conv.latest_message_body = "earlier user message"
    conv.save(update_fields=["latest_message_body"])
    calls = []
    monkeypatch.setattr(order_views, "_post_edge_chat_message", lambda *args, **kwargs: calls.append(args) or posted)

    resp = api.patch(f"/api/v1/orders/{order.id}/", {"status": "cancelled"},
                     content_type="application/json", **seller_header)

    assert resp.status_code == 200
    assert len(calls) == 1
    conv.refresh_from_db()
    expected = "[SYSTEM:order.notify.seller_rejected] System Notification" if posted else "earlier user message"
    assert conv.latest_message_body == expected


def test_edge_chat_post_failure_before_the_request_is_logged_not_raised(order, settings, monkeypatch):
    # `url` used to be assigned inside the try, after the token was signed, so
    # a failure there made the except branch's own log call raise instead.
    from rest_framework_simplejwt.backends import TokenBackend
    import orders.views.orders as order_views

    settings.EDGE_CHAT_JWT_SECRET = "x" * 32
    settings.EDGE_CHAT_URL = "https://edge.example"

    def boom(self, payload):
        raise RuntimeError("signing failed")

    monkeypatch.setattr(TokenBackend, "encode", boom)
    conv = Conversation.objects.get(listing=order.listing, buyer=order.buyer)

    assert order_views._post_edge_chat_message(conv, order.buyer, "[SYSTEM:x]", log_prefix="Test") is False

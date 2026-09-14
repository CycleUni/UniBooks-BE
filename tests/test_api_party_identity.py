"""Telling same-named users apart, and linking an order to its chat.

Orders and the inbox carry the other party's school and avatar, because a
display name is not unique; orders also carry their conversation id. The
order list reads these per row, hence the query-count guard.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import RegionVerification, School
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from orders.models import Order

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


@pytest.fixture
def book(db):
    return Book.objects.create(region_id='TW', isbn13="9781111111111", title="Identity Book", source="manual")


_counter = {"n": 0}


def make_user(first="周", last="恭煥", school=None):
    _counter["n"] += 1
    u = User.objects.create_user(
        email=f"user{_counter['n']}@test.edu.tw", first_name=first, last_name=last, password=PASSWORD
    )
    RegionVerification.objects.create(
        user=u, region_id='TW', school=school, edu_email=u.email, verified_at=timezone.now()
    )
    return u


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def make_listing(book, seller, status='active'):
    return Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new", status=status
    )


# ---------------------------------------------------------------------
# Orders: other party's identity and the conversation link
# ---------------------------------------------------------------------


@pytest.fixture
def schools(db):
    return (
        School.objects.create(region_id='TW', name="North University", email_domain="north.edu.tw",
                              translations={"zh-TW": {"name": "北大"}}),
        School.objects.create(region_id='TW', name="South University", email_domain="south.edu.tw"),
    )


def _order_with_chat(book, buyer, seller):
    listing = make_listing(book, seller)
    conv = Conversation.objects.create(listing=listing, buyer=buyer)
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        total_amount=100, status='pending',
    )
    return order, conv


def _orders_by_id(api, user, **extra):
    resp = api.get("/api/v1/orders/", HTTP_X_REGION='TW', **bearer(user), **extra)
    assert resp.status_code == 200
    body = resp.json()
    rows = body['results'] if isinstance(body, dict) else body
    return {r['id']: r for r in rows}


def test_orders_tell_same_named_buyers_apart_by_school(api, book, schools):
    north, south = schools
    seller = make_user(first="Se", last="Ller", school=north)
    # Same display name, different schools.
    buyer_a = make_user(school=north)
    buyer_b = make_user(school=south)
    User.objects.filter(pk=buyer_b.pk).update(avatar_url="https://example.com/b.png")
    order_a, _ = _order_with_chat(book, buyer_a, seller)
    order_b, _ = _order_with_chat(book, buyer_b, seller)

    rows = _orders_by_id(api, seller, HTTP_ACCEPT_LANGUAGE='en')
    a, b = rows[str(order_a.id)], rows[str(order_b.id)]
    assert a['buyer_name'] == b['buyer_name']
    assert a['buyer_school_name'] == "North University"
    assert b['buyer_school_name'] == "South University"
    assert b['buyer_avatar_url'] == "https://example.com/b.png"
    assert a['seller_school_name'] == "North University"
    # Never the email, which is the private identifier.
    assert buyer_a.email not in str(a)

    zh = _orders_by_id(api, seller, HTTP_ACCEPT_LANGUAGE='zh-TW')
    assert zh[str(order_a.id)]['buyer_school_name'] == "北大"


def test_order_links_its_own_conversation(api, book):
    seller = make_user(first="Se", last="Ller")
    buyer = make_user()
    order, conv = _order_with_chat(book, buyer, seller)
    # Another buyer's chat on the same listing must not be picked up.
    other_buyer = make_user()
    Conversation.objects.create(listing=order.listing, buyer=other_buyer)

    assert _orders_by_id(api, buyer)[str(order.id)]['conversation_id'] == str(conv.id)
    assert _orders_by_id(api, seller)[str(order.id)]['conversation_id'] == str(conv.id)


def test_order_hides_a_conversation_the_viewer_deleted(api, book):
    seller = make_user(first="Se", last="Ller")
    buyer = make_user()
    order, conv = _order_with_chat(book, buyer, seller)
    conv.buyer_deleted_at = timezone.now()
    conv.save()

    # Gone from the buyer's inbox, so no link for them; still the seller's.
    assert _orders_by_id(api, buyer)[str(order.id)]['conversation_id'] is None
    assert _orders_by_id(api, seller)[str(order.id)]['conversation_id'] == str(conv.id)


def test_order_list_queries_do_not_grow_per_order(api, book, schools):
    north, south = schools
    seller = make_user(first="Se", last="Ller", school=north)
    for school in (north, south):
        _order_with_chat(book, make_user(school=school), seller)

    def count():
        with CaptureQueriesContext(connection) as ctx:
            resp = api.get("/api/v1/orders/", HTTP_X_REGION='TW', **bearer(seller))
        assert resp.status_code == 200
        return len(ctx.captured_queries)

    count()  # warm-up: the first request also fills per-process caches
    few = count()
    for school in (north, south, north, south):
        _order_with_chat(book, make_user(school=school), seller)
    assert count() == few


# ---------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------


def test_inbox_row_carries_other_party_school(api, book, schools):
    north, south = schools
    seller = make_user(first="Se", last="Ller", school=north)
    buyer = make_user(school=south)
    listing = make_listing(book, seller)
    Conversation.objects.create(listing=listing, buyer=buyer)

    def rows(user):
        resp = api.get("/api/v1/messaging/conversations/", HTTP_X_REGION='TW', HTTP_ACCEPT_LANGUAGE='en', **bearer(user))
        assert resp.status_code == 200
        body = resp.json()
        return body['results'] if isinstance(body, dict) else body

    assert rows(seller)[0]['other_party_school_name'] == "South University"
    assert rows(buyer)[0]['other_party_school_name'] == "North University"

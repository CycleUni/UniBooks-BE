"""Hiding the Google avatar from other users (User.show_avatar).

The avatar is copied from the user's Google account on sign-in. With the
switch off it must be gone from everything someone else reads — the public
profile, listings, the inbox and orders — while the owner still sees it.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import RegionVerification
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from orders.models import Order

User = get_user_model()
AVATAR = "https://lh3.googleusercontent.com/a/fake-avatar"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return Client()


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def make_user(email, avatar_url=""):
    u = User.objects.create_user(
        email=email, first_name="Te", last_name="St", password="test-only-password-123", avatar_url=avatar_url,
    )
    RegionVerification.objects.create(user=u, region_id='TW', edu_email=email, verified_at=timezone.now())
    return u


@pytest.fixture
def deal(db):
    """A seller with a Google avatar, a buyer, and a chat and order between them."""
    seller = make_user("seller@test.edu.tw", avatar_url=AVATAR)
    buyer = make_user("buyer@test.edu.tw")
    book = Book.objects.create(region_id='TW', isbn13="9781111111112", title="Avatar Book", source="manual")
    listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new", status='active',
    )
    Conversation.objects.create(listing=listing, buyer=buyer, latest_message_body="hi")
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        total_amount=100, status='pending',
    )
    return seller, buyer, listing, order


def _rows(resp):
    assert resp.status_code == 200
    body = resp.json()
    return body['results'] if isinstance(body, dict) else body


def _avatars_seen_by_buyer(api, seller, buyer, listing):
    """The seller's avatar from every place another user sees it."""
    return {
        'profile': api.get(f"/api/v1/auth/users/{seller.pk}/", HTTP_X_REGION='TW').json()['avatar_url'],
        'listing': api.get(f"/api/v1/listings/{listing.pk}/", HTTP_X_REGION='TW').json()['seller_avatar_url'],
        'inbox': _rows(api.get("/api/v1/messaging/conversations/", HTTP_X_REGION='TW', **bearer(buyer)))[0]['other_party_avatar_url'],
        'order': _rows(api.get("/api/v1/orders/", HTTP_X_REGION='TW', **bearer(buyer)))[0]['seller_avatar_url'],
    }


def test_avatar_is_shown_by_default(api, deal):
    seller, buyer, listing, _ = deal
    assert seller.show_avatar is True
    assert set(_avatars_seen_by_buyer(api, seller, buyer, listing).values()) == {AVATAR}


def test_hiding_the_avatar_removes_it_everywhere_others_look(api, deal):
    seller, buyer, listing, _ = deal
    # Warm the listing cache first: the switch must not wait for it to expire.
    _avatars_seen_by_buyer(api, seller, buyer, listing)

    resp = api.patch("/api/v1/auth/me/", {"show_avatar": False}, content_type="application/json", **bearer(seller))
    assert resp.status_code == 200
    assert resp.json()['show_avatar'] is False

    assert set(_avatars_seen_by_buyer(api, seller, buyer, listing).values()) == {''}
    assert AVATAR not in str(api.get("/api/v1/listings/", HTTP_X_REGION='TW').content)


def test_owner_still_sees_their_hidden_avatar(api, deal):
    seller, *_ = deal
    User.objects.filter(pk=seller.pk).update(show_avatar=False)

    me = api.get("/api/v1/auth/me/", HTTP_X_REGION='TW', **bearer(seller)).json()
    assert me['avatar_url'] == AVATAR
    assert me['show_avatar'] is False


def test_showing_it_again_restores_it(api, deal):
    seller, buyer, listing, _ = deal
    User.objects.filter(pk=seller.pk).update(show_avatar=False)

    resp = api.patch("/api/v1/auth/me/", {"show_avatar": True}, content_type="application/json", **bearer(seller))
    assert resp.status_code == 200
    assert set(_avatars_seen_by_buyer(api, seller, buyer, listing).values()) == {AVATAR}


def test_show_avatar_must_be_a_boolean(api, deal):
    seller, *_ = deal
    resp = api.patch("/api/v1/auth/me/", {"show_avatar": "no"}, content_type="application/json", **bearer(seller))
    assert resp.status_code == 400
    seller.refresh_from_db()
    assert seller.show_avatar is True

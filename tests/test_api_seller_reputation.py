"""Seller reputation on listings.

Listings carry the seller's rating, review count and completed sales so buyers
can see the "two-way reviews" the home page promises. The book page and the
listing feed serialize many sellers at once, hence the query-count guard.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import RegionVerification
from catalog.models import Book
from listings.models import Listing
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


@pytest.fixture
def book(db):
    return Book.objects.create(region_id='TW', isbn13="9781111111111", title="Reputation Book", source="manual")


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


def make_listing(book, seller, status='active'):
    return Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new", status=status
    )


def completed_sale(book, seller, rating=None, no_show=False):
    """A completed order from a fresh buyer, optionally reviewed by them."""
    buyer = make_user(first="Buyer", last=str(_counter["n"]))
    listing = make_listing(book, seller, status='sold')
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', buyer=buyer, seller=seller, listing=listing,
        total_amount=100, status='completed',
    )
    if rating is not None or no_show:
        Review.objects.create(order=order, reviewer=buyer, reviewee=seller, rating=rating, is_no_show=no_show)
    return order


# ---------------------------------------------------------------------
# Seller reputation on listings
# ---------------------------------------------------------------------


def _listing_results(api, book):
    resp = api.get(f"/api/v1/books/?id={book.id}", HTTP_X_REGION='TW')
    assert resp.status_code == 200
    return {r['seller']: r for r in resp.json()['listings']['results']}


def test_listing_carries_seller_rating_reviews_and_sales(api, book):
    seller = make_user()
    make_listing(book, seller)
    completed_sale(book, seller, rating=5)
    completed_sale(book, seller, rating=4)
    # A no-show report has no rating; it must not count as a review or pull
    # the average down.
    completed_sale(book, seller, no_show=True)

    row = _listing_results(api, book)[seller.id]
    assert row['seller_review_count'] == 2
    assert row['seller_average_rating'] == 4.5
    assert row['seller_completed_sales'] == 3


def test_new_seller_has_no_rating_rather_than_zero(api, book):
    seller = make_user()
    listing = make_listing(book, seller)

    row = _listing_results(api, book)[seller.id]
    assert row['seller_review_count'] == 0
    assert row['seller_average_rating'] is None
    assert row['seller_completed_sales'] == 0

    detail = api.get(f"/api/v1/listings/{listing.id}/", HTTP_X_REGION='TW').json()
    assert detail['seller_average_rating'] is None
    assert detail['seller_review_count'] == 0


def test_listing_detail_and_feed_carry_seller_rating(api, book):
    seller = make_user()
    listing = make_listing(book, seller)
    completed_sale(book, seller, rating=3)

    detail = api.get(f"/api/v1/listings/{listing.id}/", HTTP_X_REGION='TW').json()
    assert detail['seller_average_rating'] == 3.0
    assert detail['seller_review_count'] == 1
    assert detail['seller_completed_sales'] == 1

    feed = api.get("/api/v1/listings/", HTTP_X_REGION='TW').json()
    rows = feed['results'] if isinstance(feed, dict) else feed
    assert rows[0]['seller_review_count'] == 1


def _count_queries(api, url, **extra):
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        resp = api.get(url, HTTP_X_REGION='TW', **extra)
    assert resp.status_code == 200
    return len(ctx.captured_queries)


def test_seller_stats_do_not_add_queries_per_listing(api, book):
    """The book page lists every seller's copy. Reading each seller's rating
    through the User properties would be three queries per card."""
    for _ in range(2):
        seller = make_user()
        make_listing(book, seller)
        completed_sale(book, seller, rating=5)
    _count_queries(api, f"/api/v1/books/?id={book.id}")  # warm-up, as below
    book_few = _count_queries(api, f"/api/v1/books/?id={book.id}")
    feed_few = _count_queries(api, "/api/v1/listings/")

    for _ in range(4):
        seller = make_user()
        make_listing(book, seller)
        completed_sale(book, seller, rating=4)
    assert _count_queries(api, f"/api/v1/books/?id={book.id}") == book_few
    assert _count_queries(api, "/api/v1/listings/") == feed_few

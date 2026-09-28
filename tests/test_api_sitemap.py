"""The dynamic sitemap (GET /api/v1/sitemap.xml, listings/sitemap.py)."""

import xml.etree.ElementTree as ET

import pytest
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from catalog.models import Book
from listings.models import Listing

NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def seller(db):
    return get_user_model().objects.create_user(
        email="sitemap@test.edu.tw", first_name="Site", last_name="Map", password="test-only-password-123"
    )


def _list(book, seller, status='active', region='TW', currency='TWD'):
    return Listing.objects.create(
        region_id=region, currency_id=currency, book=book, seller=seller,
        price=100, condition="new", status=status,
    )


def _locs(resp):
    root = ET.fromstring(resp.content)
    return [el.text for el in root.findall('sm:url/sm:loc', NS)]


@override_settings(FRONTEND_URL='https://unibooks.app/')
def test_lists_books_and_listings_that_are_for_sale(api, seller):
    book = Book.objects.create(region_id='TW', isbn13="9780000000001", title="On Sale", source="manual")
    listing = _list(book, seller)

    resp = api.get("/api/v1/sitemap.xml")

    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/xml")
    assert _locs(resp) == [
        "https://unibooks.app/tw/book?isbn=9780000000001",
        f"https://unibooks.app/tw/listing/{listing.id}",
    ]


@override_settings(FRONTEND_URL='https://unibooks.app')
def test_leaves_out_what_is_no_longer_for_sale(api, seller):
    sold = Book.objects.create(region_id='TW', isbn13="9780000000002", title="Sold", source="manual")
    for status in ('reserved', 'sold', 'removed'):
        _list(sold, seller, status=status)
    Book.objects.create(region_id='TW', isbn13="9780000000003", title="Never Listed", source="manual")

    assert _locs(api.get("/api/v1/sitemap.xml")) == []


@override_settings(FRONTEND_URL='https://unibooks.app')
def test_book_without_isbn_uses_its_id_and_each_region_its_own_prefix(api, seller):
    no_isbn = Book.objects.create(region_id='TW', isbn13=None, title="No ISBN", source="manual")
    hk_book = Book.objects.create(region_id='HK', isbn13="9780000000004", title="HK", source="manual")
    _list(no_isbn, seller)
    _list(hk_book, seller, region='HK', currency='HKD')

    locs = _locs(api.get("/api/v1/sitemap.xml"))

    assert f"https://unibooks.app/tw/book?id={no_isbn.id}" in locs
    assert "https://unibooks.app/hk/book?isbn=9780000000004" in locs


@override_settings(FRONTEND_URL='https://unibooks.app')
def test_a_book_with_several_listings_appears_once(api, seller):
    book = Book.objects.create(region_id='TW', isbn13="9780000000005", title="Popular", source="manual")
    _list(book, seller)
    _list(book, seller)

    book_locs = [loc for loc in _locs(api.get("/api/v1/sitemap.xml")) if "/book?" in loc]
    assert book_locs == ["https://unibooks.app/tw/book?isbn=9780000000005"]

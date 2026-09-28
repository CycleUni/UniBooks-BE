"""The dynamic sitemap (listings/sitemap.py): an index at
/api/v1/sitemap.xml naming one sitemap per kind, region and page."""

import gzip
import xml.etree.ElementTree as ET

import pytest
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from catalog.models import Book
from listings import sitemap
from listings.models import Listing

NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

pytestmark = pytest.mark.usefixtures('frontend_url')


@pytest.fixture
def frontend_url():
    with override_settings(FRONTEND_URL='https://unibooks.app/'):
        yield


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


def _book(isbn, region='TW'):
    return Book.objects.create(region_id=region, isbn13=isbn, title=f"Book {isbn}", source="manual")


def _list(book, seller, status='active', region='TW', currency='TWD'):
    return Listing.objects.create(
        region_id=region, currency_id=currency, book=book, seller=seller,
        price=100, condition="new", status=status,
    )


def _index(api):
    resp = api.get("/api/v1/sitemap.xml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.content)
    assert root.tag == f"{{{NS['sm']}}}sitemapindex"
    return [el.text for el in root.findall('sm:sitemap/sm:loc', NS)]


def _locs(api, path):
    resp = api.get(path)
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/xml")
    return [el.text for el in ET.fromstring(resp.content).findall('sm:url/sm:loc', NS)]


def test_index_names_one_sitemap_per_kind_and_region_that_has_pages(api, seller):
    _list(_book("9780000000001"), seller)
    _list(_book("9780000000002", region='HK'), seller, region='HK', currency='HKD')

    assert sorted(_index(api)) == [
        "http://testserver/api/v1/sitemap/books-hk-1.xml",
        "http://testserver/api/v1/sitemap/books-tw-1.xml",
        "http://testserver/api/v1/sitemap/listings-hk-1.xml",
        "http://testserver/api/v1/sitemap/listings-tw-1.xml",
    ]


def test_sections_list_the_pages_for_sale(api, seller):
    book = _book("9780000000001")
    listing = _list(book, seller)

    assert _locs(api, "/api/v1/sitemap/books-tw-1.xml") == ["https://unibooks.app/tw/book?isbn=9780000000001"]
    assert _locs(api, "/api/v1/sitemap/listings-tw-1.xml") == [f"https://unibooks.app/tw/listing/{listing.id}"]


def test_leaves_out_what_is_no_longer_for_sale(api, seller):
    sold = _book("9780000000002")
    for status in ('reserved', 'sold', 'removed'):
        _list(sold, seller, status=status)
    _book("9780000000003")  # never listed

    assert _index(api) == []
    assert api.get("/api/v1/sitemap/books-tw-1.xml").status_code == 404


def test_book_without_isbn_uses_its_id(api, seller):
    no_isbn = _book(None)
    _list(no_isbn, seller)

    assert _locs(api, "/api/v1/sitemap/books-tw-1.xml") == [f"https://unibooks.app/tw/book?id={no_isbn.id}"]


def test_a_book_with_several_listings_appears_once(api, seller):
    book = _book("9780000000005")
    _list(book, seller)
    _list(book, seller)

    assert _locs(api, "/api/v1/sitemap/books-tw-1.xml") == ["https://unibooks.app/tw/book?isbn=9780000000005"]


def test_pages_split_at_page_size_in_id_order(api, seller, monkeypatch):
    monkeypatch.setattr(sitemap, 'PAGE_SIZE', 2)
    books = [_book(f"978000000001{i}") for i in range(5)]
    for book in reversed(books):  # listing order must not decide the pages
        _list(book, seller)

    books_pages = [loc for loc in _index(api) if "/books-" in loc]
    assert books_pages == [f"http://testserver/api/v1/sitemap/books-tw-{p}.xml" for p in (1, 2, 3)]

    seen = []
    for page in (1, 2, 3):
        seen += _locs(api, f"/api/v1/sitemap/books-tw-{page}.xml")
    assert seen == [f"https://unibooks.app/tw/book?isbn={b.isbn13}" for b in books]
    assert api.get("/api/v1/sitemap/books-tw-4.xml").status_code == 404


def test_listings_added_after_the_bounds_land_on_exactly_one_page(api, seller, monkeypatch):
    """Pages are cut by id range, not LIMIT/OFFSET: a listing created after
    the index was built joins whichever page its (random, UUID) id falls in,
    and never shows up twice or pushes another listing off a page."""
    monkeypatch.setattr(sitemap, 'PAGE_SIZE', 2)
    book = _book("9780000000001")
    for _ in range(5):
        _list(book, seller)
    pages = [loc for loc in _index(api) if "/listings-" in loc]
    assert len(pages) == 3

    for _ in range(4):
        _list(book, seller)

    seen = []
    for page in (1, 2, 3):
        seen += _locs(api, f"/api/v1/sitemap/listings-tw-{page}.xml")
    expected = sorted(f"https://unibooks.app/tw/listing/{pk}" for pk in Listing.objects.values_list('id', flat=True))
    assert sorted(seen) == expected


@pytest.mark.parametrize('path', [
    "/api/v1/sitemap/books-xx-1.xml",     # no such region
    "/api/v1/sitemap/sellers-tw-1.xml",   # not a kind the sitemap has
    "/api/v1/sitemap/books-tw-0.xml",
    "/api/v1/sitemap/books-TW-1.xml",
])
def test_unknown_sections_are_not_found(api, seller, path):
    _list(_book("9780000000001"), seller)
    assert api.get(path).status_code == 404


def test_served_gzipped_to_crawlers_that_accept_it(api, seller):
    # Django leaves bodies under 200 bytes uncompressed; a few URLs clear that.
    for i in range(5):
        _list(_book(f"978000000000{i}"), seller)

    resp = api.get("/api/v1/sitemap/books-tw-1.xml", HTTP_ACCEPT_ENCODING="gzip")

    assert resp["Content-Encoding"] == "gzip"
    assert b"9780000000004" in gzip.decompress(resp.content)

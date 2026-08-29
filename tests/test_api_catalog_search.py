"""API tests for catalog (book detail, manual create, Google Books service) and search."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

from accounts.services import issue_tokens
from catalog.models import Book
from catalog.services import (
    get_google_books_by_isbn, search_google_books,
    get_open_library_book_by_isbn, search_open_library_books,
    get_isbnnet_book_by_isbn,
    GoogleBooksRateLimited, describe_source,
    _NOT_FOUND_CACHE_TTL,
)
from listings.models import Listing
from subscriptions.models import Subscription

User = get_user_model()

PASSWORD = "test-only-password-123"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="catalog-user@example.com", first_name="Catalog", last_name="User", password=PASSWORD
    )


@pytest.fixture
def auth_header(user):
    tokens = issue_tokens(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}


@pytest.fixture
def book(db, user):
    book = Book.objects.create(region_id='TW', isbn13="9782222222222", title="Catalog Book", source="manual")
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=user, price=150, condition="noted", status="active")
    return book


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------
# Book detail
# ---------------------------------------------------------------------


def test_book_detail_includes_listings_and_waitlist(api, book, user):
    Subscription.objects.create(region_id='TW', user=user, book=book)
    resp = api.get(f"/api/v1/books/?id={book.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Catalog Book"
    # book detail's nested "listings" is paginated (catalog.views); results live under ["results"].
    assert len(body["listings"]["results"]) == 1
    assert body["listings"]["results"][0]["seller_name"] == "Catalog User"
    assert body["waiting_count"] == 1
    assert body["is_subscribed"] is False  # anonymous request


def test_book_detail_reports_subscription_for_authed_user(api, book, user, auth_header):
    sub = Subscription.objects.create(region_id='TW', user=user, book=book)
    resp = api.get(f"/api/v1/books/?id={book.id}", **auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_subscribed"] is True
    assert body["subscription_id"] == str(sub.id)


def test_book_detail_not_found(api, db):
    resp = api.get("/api/v1/books/999999/")
    assert resp.status_code == 404


def test_book_detail_non_numeric_id_returns_clean_404(api, db):
    # A non-numeric `id` query param must not crash the raw
    # Book.objects.get(id=...) lookup with an unhandled ValueError.
    # No isbn is supplied, so the view first tries Google Books (mocked away
    # here to avoid a real network call) before falling through to the id
    # lookup that used to raise an unhandled ValueError.
    with mock.patch("catalog.views.get_google_books_by_isbn", return_value=None):
        resp = api.get("/api/v1/books/?id=not-a-valid-id")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------
# Manual book creation
# ---------------------------------------------------------------------


def test_manual_book_create_parses_source(api, auth_header, db):
    resp = api.post(
        "/api/v1/books/manual/",
        {"isbn13": "9783333333333", "title": "Google Book", "source": "google_api"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 201
    book = Book.objects.get(isbn13="9783333333333")
    assert book.source == "google_api"

    resp2 = api.post(
        "/api/v1/books/manual/",
        {"isbn13": "9783333333334", "title": "Invalid Source", "source": "fake"},
        content_type="application/json",
        **auth_header,
    )
    assert resp2.status_code == 201
    book2 = Book.objects.get(isbn13="9783333333334")
    assert book2.source == "manual"


def test_manual_book_create_requires_auth_and_valid_payload(api, auth_header, db):
    assert (
        api.post(
            "/api/v1/books/manual/", {"title": "X"}, content_type="application/json"
        ).status_code
        == 401
    )
    resp = api.post(
        "/api/v1/books/manual/", {}, content_type="application/json", **auth_header
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# Google Books service (mocked HTTP)
# ---------------------------------------------------------------------


def _fake_response(payload, status_code=200):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


GOOGLE_PAYLOAD = {
    "totalItems": 1,
    "items": [
        {
            "volumeInfo": {
                "title": "Fetched Book",
                "authors": ["Author One", "Author Two"],
                "publisher": "Pub",
                "publishedDate": "2020",
                "imageLinks": {"thumbnail": "https://covers.invalid/x.jpg"},
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": "9784444444444"}
                ],
            }
        }
    ],
}


def test_get_google_books_by_isbn_parses_and_caches(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(GOOGLE_PAYLOAD)) as get:
        result = get_google_books_by_isbn("9784444444444")
        assert result["title"] == "Fetched Book"
        assert result["authors"] == "Author One, Author Two"
        assert result["publisher"] == "Pub"
        assert result["published_date"] == "2020"
        # Second call hits the cache, not the network
        again = get_google_books_by_isbn("9784444444444")
        assert again == result
        assert get.call_count == 1


def test_get_google_books_by_isbn_handles_no_results_and_errors(db):
    with mock.patch(
        "catalog.services.requests.get",
        return_value=_fake_response({"totalItems": 0}),
    ):
        assert get_google_books_by_isbn("0000000000000") is None
    with mock.patch(
        "catalog.services.requests.get",
        side_effect=__import__("requests").RequestException("boom"),
    ):
        assert get_google_books_by_isbn("0000000000000") is None


def test_get_google_books_by_isbn_negative_caches_not_found(db):
    # A confirmed "zero results" response should be cached too, so a repeated
    # lookup for the same nonexistent ISBN does not hit the network again.
    with mock.patch(
        "catalog.services.requests.get",
        return_value=_fake_response({"totalItems": 0}),
    ) as get:
        assert get_google_books_by_isbn("1111111111111") is None
        assert get_google_books_by_isbn("1111111111111") is None
        assert get.call_count == 1


def test_search_google_books_negative_caches_not_found(db):
    with mock.patch(
        "catalog.services.requests.get",
        return_value=_fake_response({"totalItems": 0, "items": []}),
    ) as get:
        assert search_google_books("no-such-book-xyz") == []
        assert search_google_books("no-such-book-xyz") == []
        assert get.call_count == 1


def test_search_google_books_parses_and_caches(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(GOOGLE_PAYLOAD)) as get:
        results = search_google_books("fetched")
        assert results == [
            {
                "title": "Fetched Book",
                "authors": "Author One, Author Two",
                "publisher": "Pub",
                "published_date": "2020",
                "cover_url": "https://covers.invalid/x.jpg",
                "isbn": "9784444444444",
            }
        ]
        assert search_google_books("fetched") == results
        assert get.call_count == 1


def test_search_google_books_returns_empty_on_error(db):
    with mock.patch(
        "catalog.services.requests.get",
        side_effect=__import__("requests").RequestException("boom"),
    ):
        assert search_google_books("anything") == []


def test_search_google_books_degrades_gracefully_on_cache_backend_error(db):
    # A Redis hiccup (timeout, connection reset) must not crash the request —
    # this cache is a performance optimization over Google Books, not a
    # correctness requirement, so a broken cache should just behave like a
    # cold one and still serve a live result.
    with mock.patch("catalog.services.cache.get", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.cache.set", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.requests.get", return_value=_fake_response(GOOGLE_PAYLOAD)):
        results = search_google_books("fetched")
    assert results == [
        {
            "title": "Fetched Book",
            "authors": "Author One, Author Two",
            "publisher": "Pub",
            "published_date": "2020",
            "cover_url": "https://covers.invalid/x.jpg",
            "isbn": "9784444444444",
        }
    ]


def test_get_google_books_by_isbn_degrades_gracefully_on_cache_backend_error(db):
    with mock.patch("catalog.services.cache.get", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.cache.set", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.requests.get", return_value=_fake_response(GOOGLE_PAYLOAD)):
        result = get_google_books_by_isbn("9784444444444")
    assert result["title"] == "Fetched Book"


# ---------------------------------------------------------------------
# Search endpoint (Google results mocked, local enrichment real)
# ---------------------------------------------------------------------

FAKE_RESULTS = [
    {"title": "Catalog Book", "authors": "Catalog Author", "cover_url": "", "isbn": "9782222222222"},
    {"title": "Unknown Book", "authors": "Nobody", "cover_url": "", "isbn": "9789999999990"},
    {"title": "No ISBN Book", "authors": "Nobody", "cover_url": "", "isbn": None},
]


def test_search_requires_query(api, db):
    resp = api.get("/api/v1/search/books/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_enriches_local_books(api, book, user):
    Subscription.objects.create(region_id='TW', user=user, book=book)
    with mock.patch("search.views.search_google_books", return_value=FAKE_RESULTS):
        resp = api.get("/api/v1/search/books/?q=catalog")
    assert resp.status_code == 200
    # Search results are paginated (search.views.BookSearchView).
    by_isbn = {item["isbn"]: item for item in resp.json()["results"]}

    hit = by_isbn["9782222222222"]
    assert hit["id"] == str(book.id)
    assert hit["activeListings"] == 1
    assert hit["minPrice"] == 150
    assert hit["waitlistCount"] == 1
    assert hit["conditions"] == ["noted"]
    assert hit["is_subscribed"] is False  # anonymous

    miss = by_isbn["9789999999990"]
    assert miss["id"] == ""
    assert miss["activeListings"] == 0
    assert miss["conditions"] == []

    # 'No ISBN Book' is dropped because it has no ISBN
    assert "" not in by_isbn


def test_search_reports_subscription_for_authed_user(api, book, user, auth_header):
    sub = Subscription.objects.create(region_id='TW', user=user, book=book)
    with mock.patch("search.views.search_google_books", return_value=FAKE_RESULTS):
        resp = api.get("/api/v1/search/books/?q=catalog", **auth_header)
    hit = next(item for item in resp.json()["results"] if item["isbn"] == "9782222222222")
    assert hit["is_subscribed"] is True
    assert hit["subscription_id"] == str(sub.id)


# ---------------------------------------------------------------------
# Open Library service (mocked HTTP)
# ---------------------------------------------------------------------

OPEN_LIBRARY_ISBN_PAYLOAD = {
    "ISBN:9784444444444": {
        "title": "OL Fetched Book",
        "authors": [{"name": "OL Author"}],
        "publishers": [{"name": "OL Pub"}],
        "publish_date": "2019",
        "cover": {"medium": "https://covers.invalid/ol.jpg"},
    }
}

OPEN_LIBRARY_SEARCH_PAYLOAD = {
    "docs": [
        {
            "title": "OL Search Book",
            "author_name": ["OL Author One", "OL Author Two"],
            "publisher": ["OL Pub"],
            "first_publish_year": 2018,
            "cover_i": 12345,
            "isbn": ["9785555555555"],
        }
    ]
}


def test_get_open_library_book_by_isbn_parses_and_caches(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(OPEN_LIBRARY_ISBN_PAYLOAD)) as get:
        result = get_open_library_book_by_isbn("9784444444444")
        assert result["title"] == "OL Fetched Book"
        assert result["authors"] == "OL Author"
        assert result["publisher"] == "OL Pub"
        assert result["published_date"] == "2019"
        assert result["cover_url"] == "https://covers.invalid/ol.jpg"
        again = get_open_library_book_by_isbn("9784444444444")
        assert again == result
        assert get.call_count == 1


def test_get_open_library_book_by_isbn_negative_caches_not_found(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({})) as get:
        assert get_open_library_book_by_isbn("0000000000000") is None
        assert get_open_library_book_by_isbn("0000000000000") is None
        assert get.call_count == 1


def test_search_open_library_books_parses_and_caches(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(OPEN_LIBRARY_SEARCH_PAYLOAD)) as get:
        results = search_open_library_books("ol query")
        assert results == [
            {
                "title": "OL Search Book",
                "authors": "OL Author One, OL Author Two",
                "publisher": "OL Pub",
                "published_date": "2018",
                "cover_url": "https://covers.openlibrary.org/b/id/12345-M.jpg",
                "isbn": "9785555555555",
            }
        ]
        assert search_open_library_books("ol query") == results
        assert get.call_count == 1


def test_search_open_library_books_requests_isbn_field_explicitly(db):
    # Open Library's search.json omits `isbn` unless explicitly requested via
    # `fields` — without it every result silently loses its ISBN and gets
    # dropped by the search endpoint's dedup step (see search/views.py).
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(OPEN_LIBRARY_SEARCH_PAYLOAD)) as get:
        search_open_library_books("field check")
    assert "isbn" in get.call_args.kwargs["params"]["fields"]


def test_search_open_library_books_prefers_isbn13_from_mixed_list(db):
    # Real Open Library docs list dozens of ISBN-10/ISBN-13 across editions
    # (e.g. "The Psychopath Test" by Jon Ronson); an ISBN-10 first in the
    # list must not win over an available ISBN-13.
    payload = {
        "docs": [
            {
                "title": "Mixed ISBN Book",
                "author_name": ["Author"],
                "cover_i": 999,
                "isbn": ["1594488010", "9781594488016", "0330492276"],
            }
        ]
    }
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(payload)):
        results = search_open_library_books("mixed isbn query")
    assert results[0]["isbn"] == "9781594488016"


def test_search_open_library_books_negative_caches_not_found(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({"docs": []})) as get:
        assert search_open_library_books("no-such-ol-book") == []
        assert search_open_library_books("no-such-ol-book") == []
        assert get.call_count == 1


# ---------------------------------------------------------------------
# Google -> Open Library automatic fallback on 429
# ---------------------------------------------------------------------


def test_get_google_books_by_isbn_raises_on_429(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({}, status_code=429)):
        with pytest.raises(GoogleBooksRateLimited):
            get_google_books_by_isbn("9786666666666")


def test_search_google_books_raises_on_429(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({}, status_code=429)):
        with pytest.raises(GoogleBooksRateLimited):
            search_google_books("rate limited query")


def test_429_short_circuits_further_google_calls_for_10_seconds(db):
    # A 429 is a property of the whole API key/quota, not of one query — once
    # we've seen it, any *other* ISBN/query lookup within the short window
    # must also skip straight to raising (and thus to the Open Library
    # fallback) instead of burning another live call against Google that
    # would just get rate-limited again.
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({}, status_code=429)) as get:
        with pytest.raises(GoogleBooksRateLimited):
            search_google_books("first query that gets 429")
        assert get.call_count == 1

        with pytest.raises(GoogleBooksRateLimited):
            get_google_books_by_isbn("9781111111112")  # unrelated ISBN
        # No second live call — short-circuited by the cached rate-limit flag.
        assert get.call_count == 1


def test_google_rate_limit_flag_cached_for_10_seconds_not_1_hour(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({}, status_code=429)), \
            mock.patch("catalog.services.cache.set", wraps=cache.set) as set_spy:
        with pytest.raises(GoogleBooksRateLimited):
            search_google_books("rate limited ttl check")

    rate_limit_calls = [
        call for call in set_spy.call_args_list
        if call.args[0] == "gb:rate_limited"
    ]
    assert rate_limit_calls, "expected the rate-limit flag to be cached"
    assert rate_limit_calls[0].kwargs.get("timeout") == 10


def test_confirmed_not_found_still_uses_the_1_hour_cache_unaffected(db):
    # Sanity check that the new short-lived rate-limit flag is a completely
    # separate mechanism from the existing "confirmed zero results" cache —
    # a genuine empty result must still get the full negative-cache TTL.
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({"totalItems": 0, "items": []})), \
            mock.patch("catalog.services.cache.set", wraps=cache.set) as set_spy:
        assert search_google_books("truly no results query") == []

    not_found_calls = [
        call for call in set_spy.call_args_list
        if call.args[0].startswith("gb:q:")
    ]
    assert not_found_calls
    assert not_found_calls[0].kwargs.get("timeout") == _NOT_FOUND_CACHE_TTL


def test_search_endpoint_falls_back_to_open_library_on_google_429(api, db):
    with mock.patch("search.views.search_google_books", side_effect=GoogleBooksRateLimited("429")), \
            mock.patch("search.views.search_open_library_books", return_value=[
                {"title": "Fallback Book", "authors": "A", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9787777777777"}
            ]) as ol_search:
        resp = api.get("/api/v1/search/books/?q=anything")
    assert resp.status_code == 200
    ol_search.assert_called_once()
    hit = next(item for item in resp.json()["results"] if item["isbn"] == "9787777777777")
    assert hit["source"] == "openlibrary_api"


def test_search_endpoint_isbn_falls_back_to_open_library_on_google_429(api, db):
    with mock.patch("search.views.get_google_books_by_isbn", side_effect=GoogleBooksRateLimited("429")), \
            mock.patch(
                "search.views.get_open_library_book_by_isbn",
                return_value={"title": "Fallback ISBN Book", "authors": "A", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9788888888888"},
            ) as ol_isbn:
        resp = api.get("/api/v1/search/books/?q=9788888888888")
    assert resp.status_code == 200
    ol_isbn.assert_called_once()
    hit = next(item for item in resp.json()["results"] if item["isbn"] == "9788888888888")
    assert hit["source"] == "openlibrary_api"


def test_search_endpoint_isbn_falls_back_to_keyword_search_when_isbn_lookup_empty(api, db):
    # Real-world case: Google's `isbn:` operator misses a book it does have
    # indexed (observed for a Taiwanese-publisher title), even though a plain
    # keyword search using the same digits finds it. The ISBN branch should
    # not give up just because the dedicated lookup came up empty.
    with mock.patch("search.views.get_google_books_by_isbn", return_value=None), \
            mock.patch("search.views.search_google_books", return_value=[
                {"title": "Found via keyword", "authors": "A", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9786264140720"},
                {"title": "Unrelated match", "authors": "B", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9780000000000"},
            ]) as gb_search:
        resp = api.get("/api/v1/search/books/?q=9786264140720")
    assert resp.status_code == 200
    gb_search.assert_called_once_with("9786264140720", _meta=mock.ANY)
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["isbn"] == "9786264140720"
    assert results[0]["title"] == "Found via keyword"
    assert results[0]["source"] == "google_api"


def test_search_endpoint_isbn_skips_keyword_fallback_when_local_match_exists(api, book, db):
    with mock.patch("search.views.get_google_books_by_isbn", return_value=None), \
            mock.patch("search.views.search_google_books") as gb_search:
        resp = api.get(f"/api/v1/search/books/?q={book.isbn13}")
    assert resp.status_code == 200
    gb_search.assert_not_called()
    hit = next(item for item in resp.json()["results"] if item["isbn"] == book.isbn13)
    assert hit["title"] == book.title


def test_search_endpoint_explicit_openlibrary_engine_skips_google(api, db):
    with mock.patch("search.views.search_google_books") as gb_search, \
            mock.patch("search.views.search_open_library_books", return_value=[
                {"title": "OL Only Book", "authors": "A", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9789999999991"}
            ]):
        resp = api.get("/api/v1/search/books/?q=anything&engine=openlibrary")
    assert resp.status_code == 200
    gb_search.assert_not_called()
    hit = next(item for item in resp.json()["results"] if item["isbn"] == "9789999999991")
    assert hit["source"] == "openlibrary_api"


def test_search_endpoint_invalid_engine_falls_back_to_google_default(api, db):
    with mock.patch("search.views.search_google_books", return_value=[]) as gb_search:
        resp = api.get("/api/v1/search/books/?q=anything&engine=bogus")
    assert resp.status_code == 200
    gb_search.assert_called_once()


# ---------------------------------------------------------------------
# debug_source: dev-only provenance label (not part of the UI contract)
# ---------------------------------------------------------------------


def test_describe_source_labels():
    assert describe_source('googlebooks', False) == 'google books'
    assert describe_source('googlebooks', True) == 'google books cache'
    assert describe_source('openlibrary', False) == 'Open Library API'
    assert describe_source('openlibrary', True) == 'Open Library API cache'
    assert describe_source('isbnnet', False) == 'ISBNnet'
    assert describe_source('isbnnet', True) == 'ISBNnet cache'


def test_search_endpoint_debug_source_reflects_live_call_then_cache_hit(api, db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(GOOGLE_PAYLOAD)) as get:
        resp1 = api.get("/api/v1/search/books/?q=fetched-once")
        assert resp1.json()["results"][0]["debug_source"] == "google books"

        resp2 = api.get("/api/v1/search/books/?q=fetched-once")
        assert resp2.json()["results"][0]["debug_source"] == "google books cache"

        assert get.call_count == 1


def test_search_endpoint_debug_source_for_open_library_engine(api, db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(OPEN_LIBRARY_SEARCH_PAYLOAD)):
        resp = api.get("/api/v1/search/books/?q=ol-query&engine=openlibrary")
    assert resp.json()["results"][0]["debug_source"] == "Open Library API"


def test_search_endpoint_debug_source_for_isbnnet_engine(api, db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(ISBNNET_PAYLOAD)) as get:
        resp1 = api.get("/api/v1/search/books/?q=9786264048668&engine=isbnnet")
        assert resp1.json()["results"][0]["debug_source"] == "ISBNnet"

        resp2 = api.get("/api/v1/search/books/?q=9786264048668&engine=isbnnet")
        assert resp2.json()["results"][0]["debug_source"] == "ISBNnet cache"

        assert get.call_count == 1


def test_search_endpoint_debug_source_marks_fallback_as_open_library(api, db):
    with mock.patch("search.views.search_google_books", side_effect=GoogleBooksRateLimited("429")), \
            mock.patch("search.views.search_open_library_books", return_value=[
                {"title": "Fallback Book", "authors": "A", "publisher": "", "published_date": "", "cover_url": "", "isbn": "9787777777778"}
            ]):
        resp = api.get("/api/v1/search/books/?q=anything2")
    hit = next(item for item in resp.json()["results"] if item["isbn"] == "9787777777778")
    assert hit["debug_source"] == "Open Library API"


def test_local_only_results_have_no_debug_source(api, book, db):
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=catalog")
    hit = next(item for item in resp.json()["results"] if item["isbn"] == book.isbn13)
    assert hit["debug_source"] is None


# ---------------------------------------------------------------------
# google_unavailable: response flag so the frontend can annotate the engine
# selector when a 429 forced the automatic Open Library fallback (only, not
# for an explicit manual engine=openlibrary choice, which never touches
# Google at all and so has nothing to report as "unavailable").
# ---------------------------------------------------------------------


def test_google_unavailable_flag_true_on_429_fallback(api, db):
    with mock.patch("search.views.search_google_books", side_effect=GoogleBooksRateLimited("429")), \
            mock.patch("search.views.search_open_library_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=anything3")
    assert resp.json()["google_unavailable"] is True


def test_google_unavailable_flag_false_on_normal_google_response(api, db):
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=anything4")
    assert resp.json()["google_unavailable"] is False


def test_google_unavailable_flag_false_when_explicit_openlibrary_chosen(api, db):
    # Manual selection must not trigger any fallback bookkeeping — Google is
    # never attempted, so there is nothing to report as "unavailable".
    with mock.patch("search.views.search_google_books") as gb_search, \
            mock.patch("search.views.search_open_library_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=anything5&engine=openlibrary")
    gb_search.assert_not_called()
    assert resp.json()["google_unavailable"] is False


# ---------------------------------------------------------------------
# ISBNnet service (mocked HTTP) & Search / BookDetail integration
# ---------------------------------------------------------------------

ISBNNET_PAYLOAD = {
    "title": "公職考試試題大補帖. 2027: 電路學與電子學",
    "authors": "張鼎, 曾誠, 劉強編著",
    "publisher": "大碩教育",
    "published_date": "2026-10",
    "cover_url": "https://pdsapp.ncl.edu.tw/api/v1/viewer/public/cover/9786264048668",
    "isbn": "9786264048668",
}


def test_get_isbnnet_book_by_isbn_parses_and_caches(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(ISBNNET_PAYLOAD)) as get:
        result = get_isbnnet_book_by_isbn("9786264048668")
        assert result["title"] == "公職考試試題大補帖. 2027: 電路學與電子學"
        assert result["authors"] == "張鼎, 曾誠, 劉強編著"
        assert result["publisher"] == "大碩教育"
        assert result["published_date"] == "2026-10"
        assert result["cover_url"] == "https://pdsapp.ncl.edu.tw/api/v1/viewer/public/cover/9786264048668"
        assert result["isbn"] == "9786264048668"
        # Second call hits the cache, not the network
        again = get_isbnnet_book_by_isbn("9786264048668")
        assert again == result
        assert get.call_count == 1


def test_get_isbnnet_book_by_isbn_negative_caches_not_found(db):
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({"error": "not_found"}, status_code=404)) as get:
        assert get_isbnnet_book_by_isbn("0000000000000") is None
        assert get_isbnnet_book_by_isbn("0000000000000") is None
        assert get.call_count == 1


def test_get_isbnnet_book_by_isbn_does_not_cache_on_502_or_error(db):
    # Upstream scrape failure (502) is transient, should not be cached.
    with mock.patch("catalog.services.requests.get", return_value=_fake_response({"error": "upstream_error"}, status_code=502)) as get:
        assert get_isbnnet_book_by_isbn("9786264048668") is None
        assert get.call_count == 1

    # Network exception should not be cached either
    with mock.patch("catalog.services.requests.get", side_effect=__import__("requests").RequestException("timeout")) as get:
        assert get_isbnnet_book_by_isbn("9786264048668") is None
        assert get.call_count == 1

    # Subsequent successful request should succeed and call live
    with mock.patch("catalog.services.requests.get", return_value=_fake_response(ISBNNET_PAYLOAD)) as get:
        result = get_isbnnet_book_by_isbn("9786264048668")
        assert result is not None
        assert result["isbn"] == "9786264048668"
        assert get.call_count == 1


def test_get_isbnnet_book_by_isbn_degrades_gracefully_on_cache_backend_error(db):
    with mock.patch("catalog.services.cache.get", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.cache.set", side_effect=ConnectionError("redis unreachable")), \
            mock.patch("catalog.services.requests.get", return_value=_fake_response(ISBNNET_PAYLOAD)):
        result = get_isbnnet_book_by_isbn("9786264048668")
    assert result["title"] == "公職考試試題大補帖. 2027: 電路學與電子學"


def test_search_endpoint_explicit_isbnnet_engine_with_isbn_query(api, db):
    with mock.patch("search.views.get_isbnnet_book_by_isbn", return_value=dict(ISBNNET_PAYLOAD)):
        resp = api.get("/api/v1/search/books/?q=9786264048668&engine=isbnnet")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    hit = results[0]
    assert hit["isbn"] == "9786264048668"
    assert hit["title"] == "公職考試試題大補帖. 2027: 電路學與電子學"
    assert hit["source"] == "isbnnet_api"
    assert hit["debug_source"] == "ISBNnet"


def test_search_endpoint_isbnnet_engine_keyword_query_falls_back_to_googlebooks(api, db):
    with mock.patch("search.views.search_google_books", return_value=FAKE_RESULTS) as gb_search:
        resp = api.get("/api/v1/search/books/?q=circuit&engine=isbnnet")
    assert resp.status_code == 200
    gb_search.assert_called_once()
    results = resp.json()["results"]
    assert len(results) >= 1
    assert results[0]["source"] == "google_api"
    assert results[0]["debug_source"] == "google books"


def test_book_detail_with_isbnnet_engine(api, db):
    with mock.patch("catalog.views.get_isbnnet_book_by_isbn", return_value=dict(ISBNNET_PAYLOAD)):
        resp = api.get("/api/v1/books/?isbn=9786264048668&engine=isbnnet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isbn13"] == "9786264048668"
    assert body["title"] == "公職考試試題大補帖. 2027: 電路學與電子學"
    assert body["source"] == "isbnnet_api"
    assert body["debug_source"] == "ISBNnet"


@pytest.fixture(autouse=True)
def setup_region_engines_fix(db):
    from core.models import Region
    from django.core.cache import cache
    Region.objects.filter(code='TW').update(search_engines=['googlebooks', 'openlibrary', 'isbnnet'])
    cache.delete('active_regions')

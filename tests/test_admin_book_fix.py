"""Strict ISBN checks on writes, and the admin's book correction and merge."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import RegionVerification, School
from accounts.services import issue_tokens
from catalog.models import Book
from catalog.services import clean_cover_url, validate_book_isbn
from core.models import AuditEvent, Region
from listings.models import Listing
from subscriptions.models import Subscription

User = get_user_model()
PASSWORD = "test-only-password-123"

# A real misread from production: 9789860629200 with its first six digits
# read wrong, check digit still valid.
MISREAD = "6770250629200"
REAL = "9789860629200"

FOUND = {
    "title": "7天攻頂TOEIC多益閱讀", "authors": "Neo講師", "publisher": '"不求人文化"',
    "published_date": "2021-06-02", "cover_url": "https://covers.example/x.jpg",
}


def _auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _user(email, **extra):
    return User.objects.create_user(email=email, password=PASSWORD, first_name="T", last_name="U", **extra)


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def data(db):
    tw = Region.objects.get(pk="TW")
    hk = Region.objects.get(pk="HK")
    ntnu = School.objects.create(region=tw, email_domain="ntnu.edu.tw", name="NTNU")
    seller = _user("seller@test.com")
    RegionVerification.objects.create(user=seller, region=tw, school=ntnu, edu_email="s@ntnu.edu.tw", verified_at=timezone.now())
    other_seller = _user("other@test.com")
    RegionVerification.objects.create(user=other_seller, region=tw, school=ntnu, edu_email="o@ntnu.edu.tw", verified_at=timezone.now())

    wrong = Book.objects.create(region=tw, title="7天功頂TOEIC", authors="Neo講師", isbn13=MISREAD, source="manual")
    listing = Listing.objects.create(region=tw, currency=tw.currency, book=wrong, seller=seller, school=ntnu, price=250)
    hk_book = Book.objects.create(region=hk, title="HK book", isbn13="9780131103627", source="manual")

    tw_admin = _user("tw-admin@test.com", is_staff=True)
    tw_admin.managed_regions.add(tw)
    hk_admin = _user("hk-admin@test.com", is_staff=True)
    hk_admin.managed_regions.add(hk)
    return {
        "tw": tw, "school": ntnu, "seller": seller, "other_seller": other_seller,
        "wrong": wrong, "listing": listing, "hk_book": hk_book,
        "tw_admin": tw_admin, "hk_admin": hk_admin,
    }


def _patch(api, user, book, body):
    return api.patch(f"/api/v1/admin/books/{book.pk}/", body, content_type="application/json", **_auth(user))


# --- validate_book_isbn ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (REAL, REAL),
    ("978-986-06-2920-0", REAL),
    (MISREAD, None),            # checksum fine, not 978/979
    ("9789860629201", None),    # wrong check digit
    ("080442957X", "080442957X"),
    ("0804429579", None),
    ("", None),
    ("abc", None),
    ("97898606292\u00b20", None),                         # superscript two: int() raised
    ("978\uff19\uff18\uff16\uff10\uff16\uff12\uff19\uff12\uff10\uff10", None),  # fullwidth digits
    ("\u0660\u0668\u0660\u0664\u0664\u0662\u0669\u0665\u0667X", None),       # Arabic-Indic ISBN-10
])
def test_validate_book_isbn(raw, expected):
    assert validate_book_isbn(raw) == expected


# --- clean_cover_url ------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("  https://covers.example/x.jpg  ", "https://covers.example/x.jpg"),
    ("http://books.google.com/thumb.jpg", "http://books.google.com/thumb.jpg"),
    ("", ""),
    ("   ", ""),
    ("javascript:alert(1)", None),
    ("ftp://example.com/x.jpg", None),
    ("https://", None),
    ("https://exa mple.com/x.jpg", None),
    ("https://example.com/" + "x" * 1024, None),
    (None, None),
    (123, None),
])
def test_clean_cover_url(raw, expected):
    assert clean_cover_url(raw) == expected


# --- writes outside the admin --------------------------------------------

def test_manual_create_refuses_a_non_isbn(api, data):
    resp = api.post(
        "/api/v1/books/manual/", {"isbn13": MISREAD, "title": "X"},
        content_type="application/json", HTTP_X_REGION="TW", **_auth(data["seller"]),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errInvalidIsbn"


def test_manual_create_stores_the_cleaned_isbn(api, data):
    resp = api.post(
        "/api/v1/books/manual/", {"isbn13": "978-0-13-110362-7", "title": "K&R"},
        content_type="application/json", HTTP_X_REGION="TW", **_auth(data["seller"]),
    )
    assert resp.status_code == 201
    assert resp.json()["isbn13"] == "9780131103627"


def test_manual_create_refuses_non_ascii_digits_without_crashing(api, data):
    resp = api.post(
        "/api/v1/books/manual/", {"isbn13": "97898606292\u00b20", "title": "X"},
        content_type="application/json", HTTP_X_REGION="TW", **_auth(data["seller"]),
    )
    assert resp.status_code == 400


def test_seller_listing_edit_refuses_a_non_isbn(api, data):
    resp = api.patch(
        f"/api/v1/listings/{data['listing'].id}/", {"isbn": "9789860629201"},
        content_type="application/json", HTTP_X_REGION="TW", **_auth(data["seller"]),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errInvalidIsbn"


# --- admin book list and detail ------------------------------------------

def _list(api, user, query=""):
    return api.get(f"/api/v1/admin/books/{query}", **_auth(user))


def test_book_list_shows_only_the_managed_regions(api, data):
    body = _list(api, data["tw_admin"]).json()
    ids = [b["id"] for b in body["results"]]
    assert data["wrong"].pk in ids
    assert data["hk_book"].pk not in ids


def test_book_list_finds_a_book_that_never_sold(api, data):
    unsold = Book.objects.create(region=data["tw"], title="Nobody bought this", source="manual")
    body = _list(api, data["tw_admin"], "?q=nobody").json()
    assert [b["id"] for b in body["results"]] == [unsold.pk]


def test_book_list_matches_a_hyphenated_isbn(api, data):
    body = _list(api, data["hk_admin"], "?q=978-0-13-110362").json()
    assert [b["id"] for b in body["results"]] == [data["hk_book"].pk]


def test_book_list_filters_by_region(api, data):
    admin = _user("super@test.com", is_staff=True, is_superuser=True)
    body = _list(api, admin, "?region=hk").json()
    assert [b["id"] for b in body["results"]] == [data["hk_book"].pk]


def test_book_list_counts_active_listings_and_requests(api, data):
    Listing.objects.create(
        region=data["tw"], currency=data["tw"].currency, book=data["wrong"],
        seller=data["other_seller"], school=data["school"], price=200, status="sold",
    )
    Subscription.objects.create(region=data["tw"], user=data["other_seller"], book=data["wrong"])
    row = next(b for b in _list(api, data["tw_admin"]).json()["results"] if b["id"] == data["wrong"].pk)
    assert row["active_listings"] == 1
    assert row["request_count"] == 1


def test_book_list_is_for_staff_only(api, data):
    assert _list(api, data["seller"]).status_code == 403


def test_book_detail_returns_the_record(api, data):
    resp = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/", **_auth(data["tw_admin"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == data["wrong"].title
    assert body["isbn13"] == MISREAD
    assert body["active_listings"] == 1


def test_book_detail_is_404_outside_the_managed_regions(api, data):
    resp = api.get(f"/api/v1/admin/books/{data['hk_book'].pk}/", **_auth(data["tw_admin"]))
    assert resp.status_code == 404


# --- admin lookup ---------------------------------------------------------

@mock.patch("adminapi.views.books.ISBN_LOOKUPS", {"googlebooks": lambda isbn, _meta=None: dict(FOUND)})
def test_lookup_returns_the_catalogue_record(api, data):
    resp = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={REAL}", **_auth(data["tw_admin"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["isbn13"] == REAL
    assert body["title"] == FOUND["title"]
    assert body["publisher"] == "不求人文化"
    assert body["source"] == "google_api"
    assert body["existing_book"] is None
    data["wrong"].refresh_from_db()
    assert data["wrong"].isbn13 == MISREAD  # read-only


@mock.patch("adminapi.views.books.ISBN_LOOKUPS", {"googlebooks": lambda isbn, _meta=None: dict(FOUND)})
def test_lookup_names_the_book_it_would_merge_into(api, data):
    right = Book.objects.create(region=data["tw"], title="Right", isbn13=REAL, source="google_api")
    body = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={REAL}", **_auth(data["tw_admin"])).json()
    assert body["existing_book"] == {"id": right.pk, "title": "Right"}


@mock.patch("adminapi.views.books.ISBN_LOOKUPS", {"googlebooks": lambda isbn, _meta=None: {
    "title": "T", "authors": None, "publisher": None, "published_date": None, "cover_url": None,
}})
def test_lookup_turns_catalogue_nulls_into_empty_text(api, data):
    body = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={REAL}", **_auth(data["tw_admin"])).json()
    assert [body[k] for k in ("title", "authors", "publisher", "published_date", "cover_url")] == ["T", "", "", "", ""]


@mock.patch("adminapi.views.books.ISBN_LOOKUPS", dict.fromkeys(("googlebooks", "isbnnet", "openlibrary"), lambda isbn, _meta=None: None))
def test_lookup_not_found(api, data):
    resp = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={REAL}", **_auth(data["tw_admin"]))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "admin.errBookLookupNotFound"


def test_lookup_refuses_a_non_isbn(api, data):
    resp = api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={MISREAD}", **_auth(data["tw_admin"]))
    assert resp.status_code == 400


def test_admin_of_another_region_cannot_reach_the_book(api, data):
    assert api.get(f"/api/v1/admin/books/{data['wrong'].pk}/lookup/?isbn={REAL}", **_auth(data["hk_admin"])).status_code == 404
    assert _patch(api, data["hk_admin"], data["wrong"], {"title": "x"}).status_code == 404


def test_non_staff_is_refused(api, data):
    assert _patch(api, data["seller"], data["wrong"], {"title": "x"}).status_code == 403


# --- admin edit -----------------------------------------------------------

def test_edit_corrects_the_record_and_clears_the_book_page(api, data):
    page = f"/api/v1/books/?isbn={MISREAD}"
    assert api.get(page, HTTP_X_REGION="TW").json()["title"] == "7天功頂TOEIC"

    resp = _patch(api, data["tw_admin"], data["wrong"], {
        "isbn13": REAL, "title": " 7天攻頂TOEIC ", "publisher": '"不求人文化"',
        "cover_url": FOUND["cover_url"], "source": "google_api",
    })
    assert resp.status_code == 200
    assert resp.json()["merged_into"] is None
    book = data["wrong"]
    book.refresh_from_db()
    assert (book.isbn13, book.title, book.publisher, book.source) == (REAL, "7天攻頂TOEIC", "不求人文化", "google_api")
    assert api.get(f"/api/v1/books/?isbn={REAL}", HTTP_X_REGION="TW").json()["title"] == "7天攻頂TOEIC"
    assert AuditEvent.objects.filter(kind="admin.book_updated", meta__book_id=book.pk).exists()


def test_edit_keeping_the_same_isbn_is_not_a_conflict(api, data):
    resp = _patch(api, data["tw_admin"], data["wrong"], {"isbn13": MISREAD})
    # The stored value is itself no ISBN, so it can't be saved again as one.
    assert resp.status_code == 400
    Book.objects.filter(pk=data["wrong"].pk).update(isbn13=REAL)
    assert _patch(api, data["tw_admin"], data["wrong"], {"isbn13": REAL, "title": "T"}).status_code == 200


@pytest.mark.parametrize("body", [
    {"title": ""},
    {"title": 3},
    {"cover_url": "javascript:alert(1)"},
    {"source": "nonsense"},
    {"isbn13": "9789860629201"},
    {"cover_url": "https://"},
    {"cover_url": "https://exa mple.com/x.jpg"},
    {"cover_url": "https://example.com/" + "x" * 1024},
])
def test_edit_refuses_bad_values(api, data, body):
    assert _patch(api, data["tw_admin"], data["wrong"], body).status_code == 400


def test_edit_refuses_a_body_that_is_not_an_object(api, data):
    assert _patch(api, data["tw_admin"], data["wrong"], ["isbn13"]).status_code == 400


def test_edit_can_clear_the_isbn(api, data):
    assert _patch(api, data["tw_admin"], data["wrong"], {"isbn13": ""}).status_code == 200
    data["wrong"].refresh_from_db()
    assert data["wrong"].isbn13 is None


# --- merge ----------------------------------------------------------------

@pytest.fixture
def right(data):
    right = Book.objects.create(region=data["tw"], title="7天攻頂TOEIC多益閱讀", isbn13=REAL, source="google_api")
    Listing.objects.create(
        region=data["tw"], currency=data["tw"].currency, book=right,
        seller=data["other_seller"], school=data["school"], price=300,
    )
    return right


def test_taken_isbn_is_a_conflict_without_merge(api, data, right):
    resp = _patch(api, data["tw_admin"], data["wrong"], {"isbn13": REAL})
    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "admin.errBookIsbnTaken", "existing_book": {"id": right.pk, "title": right.title},
    }
    assert Book.objects.filter(pk=data["wrong"].pk).exists()


def test_merge_moves_listings_and_subscriptions(api, data, right):
    wrong = data["wrong"]
    both = _user("both@test.com")
    only_wrong = _user("only@test.com")
    Subscription.objects.create(region=data["tw"], user=both, book=wrong)
    Subscription.objects.create(region=data["tw"], user=both, book=right)
    Subscription.objects.create(region=data["tw"], user=only_wrong, book=wrong)

    resp = _patch(api, data["tw_admin"], wrong, {"isbn13": REAL, "title": "ignored", "merge": True})
    assert resp.status_code == 200
    assert resp.json()["merged_into"] == right.pk

    assert not Book.objects.filter(pk=wrong.pk).exists()
    assert Listing.objects.get(pk=data["listing"].pk).book_id == right.pk
    assert right.listings.count() == 2
    assert Subscription.objects.filter(book=right, user=both).count() == 1
    assert Subscription.objects.filter(book=right, user=only_wrong).exists()
    right.refresh_from_db()
    assert right.title == "7天攻頂TOEIC多益閱讀"  # the target keeps its own details

    # Enough on record to put the misfiled book back by hand.
    meta = AuditEvent.objects.get(kind="admin.book_merged").meta
    assert meta["into_book_id"] == right.pk
    assert meta["book"]["isbn13"] == MISREAD and meta["book"]["title"] == "7天功頂TOEIC"
    assert meta["book"]["authors"] == "Neo講師" and meta["book"]["source"] == "manual"
    assert meta["listing_ids"] == [str(data["listing"].pk)]
    assert len(meta["moved_subscription_ids"]) == 1
    assert [d["user_id"] for d in meta["dropped_subscriptions"]] == [both.pk]


def test_merge_rolls_back_when_a_step_fails(api, data, right):
    with mock.patch("adminapi.views.books.merge_book_into", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _patch(api, data["tw_admin"], data["wrong"], {"isbn13": REAL, "merge": True})
    assert Book.objects.filter(pk=data["wrong"].pk).exists()
    assert not AuditEvent.objects.filter(kind="admin.book_merged").exists()


def test_a_book_taking_the_isbn_meanwhile_is_a_conflict_not_a_merge(api, data, right):
    # The first check saw no other book; by the time the locks were held one
    # had the ISBN. Merging into a row this request never locked is refused.
    with mock.patch(
        "adminapi.views.books.AdminBookDetailView.other_book_with", side_effect=[None, right],
    ):
        resp = _patch(api, data["tw_admin"], data["wrong"], {"isbn13": REAL, "merge": True})
    assert resp.status_code == 409
    assert resp.json()["error"]["existing_book"]["id"] == right.pk
    assert Book.objects.filter(pk=data["wrong"].pk).exists()


def test_the_unique_constraint_answers_409_not_500(api, data, right):
    with mock.patch(
        "adminapi.views.books.AdminBookDetailView.other_book_with", side_effect=[None, None, right],
    ):
        resp = _patch(api, data["tw_admin"], data["wrong"], {"isbn13": REAL})
    assert resp.status_code == 409
    data["wrong"].refresh_from_db()
    assert data["wrong"].isbn13 == MISREAD
    assert not AuditEvent.objects.filter(kind="admin.book_updated").exists()


def test_merge_never_crosses_regions(api, data):
    # The HK book has this ISBN, but in another region: no conflict in TW.
    resp = _patch(api, data["tw_admin"], data["wrong"], {"isbn13": data["hk_book"].isbn13})
    assert resp.status_code == 200
    assert Book.objects.filter(pk=data["hk_book"].pk).exists()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()

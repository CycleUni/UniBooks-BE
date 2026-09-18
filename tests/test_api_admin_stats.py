"""Admin statistics: region isolation, per-book counts and the leaderboard."""

from datetime import datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.models import RegionVerification, School
from accounts.services import issue_tokens
from catalog.models import Book
from core.models import Region
from listings.models import Listing
from orders.models import Order
from subscriptions.models import Subscription

User = get_user_model()
PASSWORD = "test-only-password-123"


def _auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _user(email, **extra):
    return User.objects.create_user(email=email, password=PASSWORD, first_name="T", last_name="U", **extra)


def _order(listing, buyer, status, amount, days_ago=0):
    order = Order.objects.create(
        region=listing.region, currency=listing.currency, listing=listing,
        buyer=buyer, seller=listing.seller, status=status, total_amount=amount,
    )
    if days_ago:
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return order


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def data(db):
    tw = Region.objects.get(pk="TW")
    hk = Region.objects.get(pk="HK")
    ntu = School.objects.create(region=tw, email_domain="ntu.edu.tw", name="NTU")
    hku = School.objects.create(region=hk, email_domain="hku.hk", name="HKU")

    seller = _user("seller@test.com")
    RegionVerification.objects.create(user=seller, region=tw, school=ntu, edu_email="s@ntu.edu.tw", verified_at=timezone.now())
    hk_seller = _user("hk-seller@test.com")
    RegionVerification.objects.create(user=hk_seller, region=hk, school=hku, edu_email="s@hku.hk", verified_at=timezone.now())
    buyer = _user("buyer@test.com")

    calc = Book.objects.create(region=tw, title="Calculus", authors="Stewart", isbn13="9781285740621")
    physics = Book.objects.create(region=tw, title="Physics", isbn13="9780000000001")
    unsold = Book.objects.create(region=tw, title="Calculus Workbook", isbn13="9780000000002")
    hk_book = Book.objects.create(region=hk, title="HK Economics", isbn13="9780000000003")

    def listing(book, price, region=tw, school=ntu, who=seller, status="active"):
        return Listing.objects.create(
            region=region, currency=region.currency, book=book, seller=who,
            school=school, price=price, status=status,
        )

    calc_l1 = listing(calc, 300)
    calc_l2 = listing(calc, 500, status="sold")
    phys_l = listing(physics, 200)
    hk_l = listing(hk_book, 99999, region=hk, school=hku, who=hk_seller)
    listing(unsold, 100)

    _order(calc_l1, buyer, "completed", 300)
    _order(calc_l2, buyer, "completed", 500)
    _order(calc_l1, buyer, "cancelled", 300)
    _order(calc_l1, buyer, "completed", 400, days_ago=60)  # outside a 30-day window
    _order(phys_l, buyer, "completed", 200)
    _order(phys_l, buyer, "pending", 200)
    _order(hk_l, buyer, "completed", 99999)

    # Requests: calc is listed (its request was answered), chem is not listed at all.
    reader = _user("reader@test.com")
    chem = Book.objects.create(region=tw, title="Organic Chemistry", isbn13="9780000000004")
    Subscription.objects.create(region=tw, user=buyer, book=calc, school=ntu, notified_at=timezone.now())
    Subscription.objects.create(region=tw, user=buyer, book=chem, school=ntu)
    old = Subscription.objects.create(region=tw, user=reader, book=chem, school=ntu, notified_at=timezone.now())
    Subscription.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=60))
    Subscription.objects.create(region=hk, user=reader, book=hk_book)

    tw_admin = _user("tw-admin@test.com", is_staff=True)
    tw_admin.managed_regions.add(tw)
    superuser = _user("root@test.com", is_staff=True, is_superuser=True)
    return {
        "tw": tw, "hk": hk, "calc": calc, "physics": physics, "unsold": unsold,
        "hk_book": hk_book, "chem": chem, "tw_admin": tw_admin, "superuser": superuser, "buyer": buyer,
    }


# --- access --------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/api/v1/admin/stats/overview/",
    "/api/v1/admin/stats/timeseries/",
    "/api/v1/admin/stats/books/ranking/",
    "/api/v1/admin/stats/books/requests/",
])
def test_region_is_required(api, data, url):
    resp = api.get(url, **_auth(data["superuser"]))
    assert resp.status_code == 400


@pytest.mark.parametrize("url", [
    "/api/v1/admin/stats/overview/?region=HK",
    "/api/v1/admin/stats/timeseries/?region=HK",
    "/api/v1/admin/stats/books/ranking/?region=HK",
    "/api/v1/admin/stats/books/requests/?region=HK",
])
def test_manager_cannot_read_another_region(api, data, url):
    resp = api.get(url, **_auth(data["tw_admin"]))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin.errRegionForbidden"


def test_manager_cannot_read_book_stats_of_another_region(api, data):
    resp = api.get(f"/api/v1/admin/stats/books/{data['hk_book'].pk}/?region=HK", **_auth(data["tw_admin"]))
    assert resp.status_code == 403


def test_non_staff_is_rejected(api, data):
    resp = api.get("/api/v1/admin/stats/overview/?region=TW", **_auth(data["buyer"]))
    assert resp.status_code == 403


def test_invalid_days_rejected(api, data):
    resp = api.get("/api/v1/admin/stats/overview/?region=TW&days=13", **_auth(data["tw_admin"]))
    assert resp.status_code == 400


# --- overview ------------------------------------------------------------

def test_overview_counts_only_the_requested_region(api, data):
    resp = api.get("/api/v1/admin/stats/overview/?region=tw&days=30", **_auth(data["tw_admin"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["region"] == "TW"
    assert body["currency"] == "TWD"
    orders = body["orders"]
    assert orders["new"] == 5
    assert orders["by_status"]["completed"] == 3
    assert orders["by_status"]["cancelled"] == 1
    assert orders["gmv"] == 1000  # 300 + 500 + 200; the HKD order never joins in
    assert orders["avg_order_value"] == 333
    assert orders["completion_rate"] == 0.75
    assert body["listings"]["by_status"]["active"] == 3
    assert body["listings"]["by_status"]["sold"] == 1
    assert body["users"]["total"] == 1
    assert "new_subscriptions" not in body["engagement"]
    assert body["requests"] == {"total": 3, "new": 2, "requested_books": 2, "unlisted_books": 1}
    assert body["top_schools"][0]["name"] == "NTU"
    assert body["top_schools"][0]["completed_orders"] == 3


def test_overview_all_time_includes_older_orders(api, data):
    body = api.get("/api/v1/admin/stats/overview/?region=TW&days=0", **_auth(data["tw_admin"])).json()
    assert body["since"] is None
    assert body["orders"]["by_status"]["completed"] == 4
    assert body["orders"]["gmv"] == 1400


def test_hk_overview_is_separate(api, data):
    body = api.get("/api/v1/admin/stats/overview/?region=HK", **_auth(data["superuser"])).json()
    assert body["currency"] == "HKD"
    assert body["orders"]["gmv"] == 99999
    assert body["orders"]["by_status"]["completed"] == 1


# --- timeseries ----------------------------------------------------------

def test_timeseries_is_zero_filled_and_region_scoped(api, data):
    body = api.get("/api/v1/admin/stats/timeseries/?region=TW&days=7", **_auth(data["tw_admin"])).json()
    series = body["series"]
    assert len(series) == 7
    assert series[-1]["orders"] == 5
    assert series[-1]["completed"] == 3
    assert series[-1]["gmv"] == 1000
    assert sum(d["orders"] for d in series[:-1]) == 0


def test_timeseries_all_time_is_capped(api, data):
    body = api.get("/api/v1/admin/stats/timeseries/?region=TW&days=0", **_auth(data["tw_admin"])).json()
    assert len(body["series"]) == 365
    assert sum(d["completed"] for d in body["series"]) == 4


# --- book ranking --------------------------------------------------------

def test_ranking_orders_books_by_completed_transactions(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&days=30", **_auth(data["tw_admin"])).json()
    assert body["currency"] == "TWD"
    assert body["count"] == 2  # the unsold book and the HK book are not listed
    first, second = body["results"]
    assert first["book"]["title"] == "Calculus"
    assert first["rank"] == 1
    assert first["completed_count"] == 2
    assert first["order_count"] == 3
    assert first["cancelled_count"] == 1
    assert first["gmv"] == 800
    assert first["avg_price"] == 400
    assert (first["min_price"], first["max_price"]) == (300, 500)
    assert first["active_listings"] == 1
    assert "subscriptions" not in first  # requests have their own ranking
    assert second["book"]["title"] == "Physics"
    assert second["rank"] == 2


def test_ranking_all_time(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&days=0", **_auth(data["tw_admin"])).json()
    assert body["results"][0]["completed_count"] == 3


def test_ranking_ties_share_a_rank(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&sort=orders", **_auth(data["tw_admin"])).json()
    ranks = {r["book"]["title"]: r["rank"] for r in body["results"]}
    assert ranks == {"Calculus": 1, "Physics": 2}
    _order(Listing.objects.get(book=data["physics"]), data["buyer"], "pending", 200)
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&sort=orders", **_auth(data["tw_admin"])).json()
    assert [r["rank"] for r in body["results"]] == [1, 1]


def test_ranking_search_finds_unsold_books_with_global_rank(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&q=calculus", **_auth(data["tw_admin"])).json()
    by_title = {r["book"]["title"]: r for r in body["results"]}
    assert set(by_title) == {"Calculus", "Calculus Workbook"}
    assert by_title["Calculus"]["rank"] == 1
    assert by_title["Calculus Workbook"]["rank"] is None
    assert by_title["Calculus Workbook"]["completed_count"] == 0


def test_ranking_search_by_isbn_with_hyphens(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&q=978-1285740621", **_auth(data["tw_admin"])).json()
    assert [r["book"]["title"] for r in body["results"]] == ["Calculus"]


def test_ranking_search_does_not_leak_other_regions_books(api, data):
    body = api.get("/api/v1/admin/stats/books/ranking/?region=TW&q=economics", **_auth(data["tw_admin"])).json()
    assert body["count"] == 0


def test_ranking_rejects_unknown_sort(api, data):
    resp = api.get("/api/v1/admin/stats/books/ranking/?region=TW&sort=price", **_auth(data["tw_admin"]))
    assert resp.status_code == 400


# --- single book ---------------------------------------------------------

def test_book_detail(api, data):
    resp = api.get(f"/api/v1/admin/stats/books/{data['calc'].pk}/?region=TW&days=30", **_auth(data["tw_admin"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["book"]["isbn13"] == "9781285740621"
    summary = body["summary"]
    assert summary["completed_count"] == 2
    assert summary["all_time_completed"] == 3
    assert summary["rank"] == 1
    assert body["by_status"]["cancelled"] == 1
    assert body["series"][-1] == {"date": body["series"][-1]["date"], "orders": 3, "completed": 2}
    assert body["top_schools"] == [{"id": body["top_schools"][0]["id"], "code": "NTU", "name": "NTU", "completed_orders": 2}]
    assert len(body["recent_orders"]) == 4
    assert [l["price"] for l in body["active_listings"]] == [300]


def test_book_detail_of_other_region_book_shows_no_foreign_orders(api, data):
    body = api.get(f"/api/v1/admin/stats/books/{data['hk_book'].pk}/?region=TW", **_auth(data["tw_admin"])).json()
    assert body["summary"]["completed_count"] == 0
    assert body["summary"]["gmv"] == 0
    assert body["recent_orders"] == []


def test_book_detail_404(api, data):
    resp = api.get("/api/v1/admin/stats/books/999999/?region=TW", **_auth(data["tw_admin"]))
    assert resp.status_code == 404


# --- book requests -------------------------------------------------------

def test_request_ranking_is_separate_from_transactions(api, data):
    body = api.get("/api/v1/admin/stats/books/requests/?region=TW&days=30", **_auth(data["tw_admin"])).json()
    assert body["summary"] == {"total": 3, "new": 2, "requested_books": 2, "unlisted_books": 1}
    assert [r["book"]["title"] for r in body["results"]] == ["Organic Chemistry", "Calculus"]
    chem, calc = body["results"]
    assert chem["rank"] == 1
    assert chem["request_count"] == 2
    assert chem["new_requests"] == 1  # the 60-day-old request is outside the window
    assert chem["notified_count"] == 1
    assert chem["waiting_count"] == 1
    assert chem["active_listings"] == 0
    assert chem["last_requested_at"] is not None
    assert calc["rank"] == 2
    assert calc["active_listings"] == 1
    assert calc["waiting_count"] == 0
    assert "completed_count" not in calc


def test_request_ranking_unlisted_only(api, data):
    body = api.get("/api/v1/admin/stats/books/requests/?region=TW&unlisted=1", **_auth(data["tw_admin"])).json()
    assert [(r["book"]["title"], r["rank"]) for r in body["results"]] == [("Organic Chemistry", 1)]


def test_request_ranking_search_keeps_rank(api, data):
    body = api.get("/api/v1/admin/stats/books/requests/?region=TW&q=calculus", **_auth(data["tw_admin"])).json()
    assert [(r["book"]["title"], r["rank"]) for r in body["results"]] == [("Calculus", 2)]


def test_request_ranking_is_region_scoped(api, data):
    body = api.get("/api/v1/admin/stats/books/requests/?region=HK", **_auth(data["superuser"])).json()
    assert [r["book"]["title"] for r in body["results"]] == ["HK Economics"]
    assert body["summary"]["total"] == 1


def test_book_detail_reports_requests_apart(api, data):
    body = api.get(f"/api/v1/admin/stats/books/{data['chem'].pk}/?region=TW&days=30", **_auth(data["tw_admin"])).json()
    assert "subscriptions" not in body["summary"]
    assert body["summary"]["completed_count"] == 0
    assert body["requests"] == {
        "request_count": 2, "new_requests": 1, "notified_count": 1, "waiting_count": 1,
        "last_requested_at": body["requests"]["last_requested_at"], "rank": 1,
    }


# --- today ---------------------------------------------------------------

def _local_now(region):
    from zoneinfo import ZoneInfo
    return timezone.now().astimezone(ZoneInfo(region.timezone))


def test_today_counts_from_local_midnight(api, data):
    body = api.get("/api/v1/admin/stats/overview/?region=TW&days=1", **_auth(data["tw_admin"])).json()
    assert body["days"] == 1
    local = _local_now(data["tw"])
    assert body["since"].startswith(local.date().isoformat() + "T00:00:00")
    assert body["orders"]["new"] == 5
    assert body["orders"]["gmv"] == 1000


def test_today_series_is_hourly_up_to_now(api, data):
    body = api.get("/api/v1/admin/stats/timeseries/?region=TW&days=1", **_auth(data["tw_admin"])).json()
    hour = _local_now(data["tw"]).hour
    assert body["unit"] == "hour"
    assert [d["date"] for d in body["series"]] == [f"{h:02d}:00" for h in range(hour + 1)]
    assert body["series"][-1]["orders"] == 5
    assert body["series"][-1]["completed"] == 3
    assert sum(d["orders"] for d in body["series"]) == 5


def test_daily_series_reports_its_unit(api, data):
    body = api.get("/api/v1/admin/stats/timeseries/?region=TW&days=7", **_auth(data["tw_admin"])).json()
    assert body["unit"] == "day"


def test_book_detail_today_is_hourly(api, data):
    body = api.get(f"/api/v1/admin/stats/books/{data['calc'].pk}/?region=TW&days=1", **_auth(data["tw_admin"])).json()
    assert body["series_unit"] == "hour"
    assert body["series"][-1]["orders"] == 3
    assert body["summary"]["completed_count"] == 2


# --- breakdown: school / category / course / professor ---------------------

@pytest.fixture
def academic(data):
    from core.models import Category
    tw = data["tw"]
    ntu = School.objects.get(email_domain="ntu.edu.tw")
    nccu = School.objects.create(region=tw, email_domain="nccu.edu.tw", name="NCCU",
                                 translations={"zh-TW": {"name": "政大"}})
    eng = Category.objects.create(region=tw, slug="eng", title="Engineering",
                                  translations={"zh-TW": {"title": "工學院"}})
    biz = Category.objects.create(region=tw, slug="biz", title="Business")
    seller = User.objects.get(email="seller@test.com")
    buyer = data["buyer"]

    def listing(book, school, category, course="", professor="", status="active"):
        return Listing.objects.create(region=tw, currency=tw.currency, book=book, seller=seller, school=school,
                                      category=category, course_name=course, professor_name=professor,
                                      price=100, status=status)

    calc = data["calc"]
    a = listing(calc, ntu, eng, course="Calculus ", professor="Dr. Lin")
    b = listing(calc, ntu, eng, course="calculus", professor="dr. lin", status="sold")
    c = listing(calc, nccu, biz, course="Accounting", professor="Wang")
    listing(calc, nccu, biz, course="   ")
    _order(a, buyer, "completed", 100)
    _order(b, buyer, "completed", 150)
    _order(c, buyer, "cancelled", 100)
    return {"ntu": ntu, "nccu": nccu, "eng": eng, "biz": biz}


def _breakdown(api, user, qs):
    return api.get(f"/api/v1/admin/stats/breakdown/?region=TW&{qs}", **_auth(user))


def test_breakdown_requires_known_dimension(api, data):
    assert _breakdown(api, data["tw_admin"], "by=teacher").status_code == 400
    assert _breakdown(api, data["tw_admin"], "by=course&sort=price").status_code == 400


def test_breakdown_is_region_guarded(api, data):
    resp = api.get("/api/v1/admin/stats/breakdown/?region=HK&by=school", **_auth(data["tw_admin"]))
    assert resp.status_code == 403


def test_breakdown_by_course_merges_spellings(api, data, academic):
    body = _breakdown(api, data["tw_admin"], "by=course&days=30").json()
    rows = {r["label"].casefold(): r for r in body["results"]}
    assert set(rows) == {"calculus", "accounting"}  # the blank course is not a row
    calc = rows["calculus"]
    assert (calc["orders"], calc["completed"], calc["gmv"]) == (2, 2, 250)
    assert (calc["active_listings"], calc["new_listings"]) == (1, 2)
    assert calc["rank"] == 1
    assert rows["accounting"]["rank"] is None  # no completed transaction
    assert body["summary"]["listings_with_value"] == 3
    assert body["summary"]["listings"] == 8  # fixture listings count too


def test_breakdown_by_professor_within_school(api, data, academic):
    body = _breakdown(api, data["tw_admin"], f"by=professor&school={academic['nccu'].pk}").json()
    assert [(r["label"], r["orders"], r["completed"]) for r in body["results"]] == [("Wang", 1, 0)]
    assert body["summary"] == {"groups": 1, "listings": 2, "listings_with_value": 1}


def test_breakdown_by_category_uses_localized_title(api, data, academic):
    resp = api.get("/api/v1/admin/stats/breakdown/?region=TW&by=category&lang=zh-TW", **_auth(data["tw_admin"]))
    rows = {r["label"]: r for r in resp.json()["results"]}
    assert rows["工學院"]["completed"] == 2
    assert rows["工學院"]["id"] == academic["eng"].pk
    assert rows["Business"]["orders"] == 1
    assert "" in rows  # fixture listings have no category


def test_breakdown_by_school_sorted_and_searchable(api, data, academic):
    body = _breakdown(api, data["tw_admin"], "by=school&sort=new_listings").json()
    assert body["results"][0]["label"] == "NTU"
    assert body["results"][0]["rank"] == 1
    # Labels follow the region's language; the canonical name still matches.
    found = _breakdown(api, data["tw_admin"], "by=school&sort=new_listings&q=nccu").json()
    assert [r["label"] for r in found["results"]] == ["政大"]
    assert found["results"][0]["rank"] == 2


def test_breakdown_pagination(api, data, academic):
    body = _breakdown(api, data["tw_admin"], "by=course&page_size=1&page=2").json()
    assert body["count"] == 2
    assert len(body["results"]) == 1


# --- books inside a breakdown row -------------------------------------------

def _ranking(api, user, qs):
    return api.get(f"/api/v1/admin/stats/books/ranking/?region=TW&days=30&{qs}", **_auth(user)).json()


def test_breakdown_text_rows_carry_their_key(api, data, academic):
    body = _breakdown(api, data["tw_admin"], "by=course").json()
    assert {r["key"] for r in body["results"]} == {"calculus", "accounting"}
    assert all("id" not in r for r in body["results"])


def test_books_within_a_course(api, data, academic):
    body = _ranking(api, data["tw_admin"], "course=calculus")
    assert [(r["book"]["title"], r["completed_count"], r["gmv"], r["active_listings"]) for r in body["results"]] == [
        ("Calculus", 2, 250, 1),  # the fixture's own Calculus listings name no course
    ]


def test_books_within_a_professor(api, data, academic):
    body = _ranking(api, data["tw_admin"], "professor=wang")
    assert [(r["book"]["title"], r["order_count"], r["completed_count"]) for r in body["results"]] == [("Calculus", 1, 0)]


def test_books_within_a_school_and_category(api, data, academic):
    body = _ranking(api, data["tw_admin"], f"school={academic['ntu'].pk}&category={academic['eng'].pk}")
    assert [(r["book"]["title"], r["completed_count"]) for r in body["results"]] == [("Calculus", 2)]


def test_books_without_a_category(api, data, academic):
    body = _ranking(api, data["tw_admin"], "category=none")
    assert [(r["book"]["title"], r["completed_count"]) for r in body["results"]] == [("Calculus", 2), ("Physics", 1)]


def test_books_scope_rejects_bad_ids(api, data):
    resp = api.get("/api/v1/admin/stats/books/ranking/?region=TW&category=abc", **_auth(data["tw_admin"]))
    assert resp.status_code == 400


# --- growth and retention ---------------------------------------------------

@pytest.fixture
def growth(data):
    from messaging.models import Conversation
    calc_l1 = Listing.objects.get(book=data["calc"], price=300)
    phys = Listing.objects.get(book=data["physics"])
    buyer = data["buyer"]
    chatter = _user("chatter@test.com")
    Conversation.objects.create(listing=calc_l1, buyer=buyer)       # buyer went on to order
    Conversation.objects.create(listing=phys, buyer=chatter)         # never ordered
    old = Conversation.objects.create(listing=phys, buyer=data["tw_admin"])
    Conversation.objects.filter(pk=old.pk).update(created_at=None)   # from before dates were kept
    # Completion times for two of the completed orders.
    now = timezone.now()
    done = Order.objects.filter(region=data["tw"], status="completed", listing__book=data["calc"],
                                created_at__gte=now - timedelta(days=1))
    for i, order in enumerate(done):
        Order.objects.filter(pk=order.pk).update(completed_at=order.created_at + timedelta(days=2 + 2 * i))
    # The chem request is answered: a listing appears and the requester buys it.
    chem_listing = Listing.objects.create(region=data["tw"], currency=data["tw"].currency, book=data["chem"],
                                          seller=User.objects.get(email="seller@test.com"), price=90)
    _order(chem_listing, buyer, "completed", 90)
    return {"chatter": chatter}


def test_growth_requires_staff_of_region(api, data):
    resp = api.get("/api/v1/admin/stats/growth/?region=HK", **_auth(data["tw_admin"]))
    assert resp.status_code == 403


def test_growth_figures(api, data, growth):
    body = api.get("/api/v1/admin/stats/growth/?region=TW&days=30", **_auth(data["tw_admin"])).json()

    assert body["sell_through"] == {"listings": 5, "sold": 1, "rate": 0.2}

    speed = body["speed"]
    assert speed["order_to_completion"] == {"count": 2, "avg_days": 3.0, "median_days": 3.0}
    # Ordered listings: calc_l1, calc_l2, physics, chem. calc_l1's first order is
    # the fixture's backdated one, earlier than the listing itself — skipped.
    assert speed["listing_to_first_order"]["count"] == 3
    assert speed["listing_to_first_chat"]["count"] == 2

    users = body["users"]
    assert (users["buyers"], users["sellers"], users["both"]) == (1, 1, 0)
    assert users["repeat_buyers"] == 1 and users["repeat_rate"] == 1.0

    chats = body["chat_to_order"]
    assert (chats["chats"], chats["ordered"], chats["completed"], chats["undated"]) == (2, 1, 1, 1)
    assert chats["order_rate"] == 0.5

    req = body["requests"]
    assert (req["requests"], req["notified"], req["ordered"], req["bought"]) == (2, 1, 1, 1)


def test_growth_all_time_counts_undated_chats(api, data, growth):
    body = api.get("/api/v1/admin/stats/growth/?region=TW&days=0", **_auth(data["tw_admin"])).json()
    assert body["chat_to_order"]["chats"] == 3
    assert body["requests"]["requests"] == 3  # the 60-day-old request joins in


def test_term_index_boundaries():
    from zoneinfo import ZoneInfo
    from adminapi.views.growth import term_index, term_label
    tz = ZoneInfo("Asia/Taipei")
    aug = datetime(2026, 8, 1, tzinfo=tz)
    jan = datetime(2027, 1, 31, tzinfo=tz)
    feb = datetime(2027, 2, 1, tzinfo=tz)
    assert term_index(aug, tz) == term_index(jan, tz)
    assert term_label(term_index(jan, tz)) == {"year": 2026, "season": "autumn"}
    assert term_label(term_index(feb, tz)) == {"year": 2027, "season": "spring"}
    assert term_index(feb, tz) == term_index(jan, tz) + 1


def test_retention_cohorts(api, data):
    from zoneinfo import ZoneInfo
    from adminapi.views.growth import term_index
    tz = ZoneInfo(data["tw"].timezone)
    now = timezone.now()
    first, current = term_index(now - timedelta(days=60), tz), term_index(now, tz)

    body = api.get("/api/v1/admin/stats/retention/?region=TW&terms=4", **_auth(data["tw_admin"])).json()
    assert len(body["terms"]) == 4 and len(body["cohorts"]) == 4
    by_term = {(c["term"]["year"], c["term"]["season"]): c for c in body["cohorts"]}
    label = lambda i: (i // 2, "autumn" if i % 2 else "spring")
    # Buyer and seller were both first active 60 days ago, and again now.
    cohort = by_term[label(first)]
    assert cohort["size"] == 2
    assert cohort["retained"][0] == 2
    assert cohort["retained"][-1] == 2
    assert len(cohort["retained"]) == current - first + 1
    assert cohort["rates"][0] == 1.0


def test_retention_by_role_and_validation(api, data):
    body = api.get("/api/v1/admin/stats/retention/?region=TW&role=buyer", **_auth(data["tw_admin"])).json()
    assert sum(c["size"] for c in body["cohorts"]) == 1
    assert api.get("/api/v1/admin/stats/retention/?region=TW&role=x", **_auth(data["tw_admin"])).status_code == 400
    assert api.get("/api/v1/admin/stats/retention/?region=TW&terms=9", **_auth(data["tw_admin"])).status_code == 400

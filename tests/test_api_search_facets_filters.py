import pytest
from unittest import mock
from core.models import Category, Region
from catalog.models import Book
from listings.models import Listing
from accounts.models import User

@pytest.fixture
def api():
    from django.test import Client
    return Client(HTTP_X_REGION='TW')

@pytest.fixture
def region(setup_regions):
    return Region.objects.get(code='TW')

@pytest.fixture
def test_user(db):
    return User.objects.create(email="test@example.com", first_name="Test", last_name="User")

@pytest.mark.django_db
def test_search_facets_and_filters_compatibility(api, region, test_user):
    cat = Category.objects.filter(slug="engineering", region=region).first()
    if not cat:
        cat, _ = Category.objects.get_or_create(slug="engineering", defaults={"title": "Engineering", "region": region})
    book1, _ = Book.objects.get_or_create(title="Calculus I", isbn13="9780001", region=region)
    book2, _ = Book.objects.get_or_create(title="Physics I", isbn13="9780002", region=region)
    Listing.objects.create(seller=test_user, book=book1, condition="new", price=100, status="active", course_name="Calc1", category=cat, region=region, currency=region.currency)
    Listing.objects.create(seller=test_user, book=book2, condition="like_new", price=200, status="active", course_name="Phys1", category=cat, region=region, currency=region.currency)
    
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=978000")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert "facets" in data
    assert len(data["facets"]["condition"]) == 4

@pytest.mark.django_db
def test_search_condition_filter(api, region, test_user):
    book1, _ = Book.objects.get_or_create(title="Book 1", isbn13="9780001", region=region)
    book2, _ = Book.objects.get_or_create(title="Book 2", isbn13="9780002", region=region)
    Listing.objects.create(seller=test_user, book=book1, condition="new", price=100, status="active", region=region, currency=region.currency)
    Listing.objects.create(seller=test_user, book=book2, condition="like_new", price=200, status="active", region=region, currency=region.currency)
    
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=Book&condition=new")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    facets = data["facets"]
    cond_facets = {f["value"]: f["count"] for f in facets["condition"]}
    assert cond_facets["new"] == 1
    assert cond_facets["like_new"] == 1

@pytest.mark.django_db
def test_search_price_and_stock_filter(api, region, test_user):
    book1, _ = Book.objects.get_or_create(title="Book 1", isbn13="9780001", region=region)
    book2, _ = Book.objects.get_or_create(title="Book 2", isbn13="9780002", region=region)
    book3, _ = Book.objects.get_or_create(title="Book 3", isbn13="9780003", region=region)
    Listing.objects.create(seller=test_user, book=book1, condition="new", price=50, status="active", region=region, currency=region.currency)
    Listing.objects.create(seller=test_user, book=book2, condition="new", price=150, status="active", region=region, currency=region.currency)
    Listing.objects.create(seller=test_user, book=book3, condition="new", price=50, status="sold", region=region, currency=region.currency)
    
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp = api.get("/api/v1/search/books/?q=Book&price_min=100")
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "Book 2"
    with mock.patch("search.views.search_google_books", return_value=[]):
        resp2 = api.get("/api/v1/search/books/?q=Book&in_stock=1")
    data2 = resp2.json()
    assert len(data2["results"]) == 2


@pytest.mark.django_db
def test_category_browse_reports_when_it_stopped_at_the_cap(api, region, test_user, monkeypatch):
    """Browsing a category reads the local catalogue and stops at a cap, so
    beyond it there are matches no page can reach. The response has to say so
    — otherwise the list just ends early and the user is told nothing."""
    from search import views

    monkeypatch.setattr(views, 'LOCAL_BROWSE_LIMIT', 2)
    cat, _ = Category.objects.get_or_create(
        slug="capped", defaults={"title": "Capped", "region": region}
    )
    for i in range(3):
        book = Book.objects.create(title=f"Capped {i}", isbn13=f"978100{i}", region=region)
        Listing.objects.create(
            seller=test_user, book=book, condition="new", price=100, status="active",
            category=cat, region=region, currency=region.currency,
        )

    resp = api.get(f"/api/v1/search/books/?category={cat.slug}")
    assert resp.status_code == 200
    assert resp.json()["results_truncated"] is True


@pytest.mark.django_db
def test_category_browse_is_not_flagged_when_everything_fits(api, region, test_user):
    cat, _ = Category.objects.get_or_create(
        slug="uncapped", defaults={"title": "Uncapped", "region": region}
    )
    book = Book.objects.create(title="Only One", isbn13="9781100", region=region)
    Listing.objects.create(
        seller=test_user, book=book, condition="new", price=100, status="active",
        category=cat, region=region, currency=region.currency,
    )

    resp = api.get(f"/api/v1/search/books/?category={cat.slug}")
    assert resp.status_code == 200
    assert resp.json()["results_truncated"] is False


@pytest.mark.django_db
def test_listing_filters_browse_without_a_keyword(api, region, test_user):
    # The search screen with only a condition picked: no keyword, category or
    # course, yet the filter should still find the books on sale that match.
    book1, _ = Book.objects.get_or_create(title="Book 1", isbn13="9780001", region=region)
    book2, _ = Book.objects.get_or_create(title="Book 2", isbn13="9780002", region=region)
    Listing.objects.create(seller=test_user, book=book1, condition="damaged", price=100, status="active", region=region, currency=region.currency)
    Listing.objects.create(seller=test_user, book=book2, condition="new", price=300, status="active", region=region, currency=region.currency)

    resp = api.get("/api/v1/search/books/?q=&condition=damaged")
    assert [r["title"] for r in resp.json()["results"]] == ["Book 1"]

    resp = api.get("/api/v1/search/books/?q=&price_min=200")
    assert [r["title"] for r in resp.json()["results"]] == ["Book 2"]

    resp = api.get("/api/v1/search/books/?q=&in_stock=1")
    assert len(resp.json()["results"]) == 2

    # Nothing chosen at all is still no search.
    assert api.get("/api/v1/search/books/?q=").json() == []


@pytest.mark.django_db
def test_local_count_covers_every_page(api, region, test_user):
    # "Found N at this school" is read from local_count. The client sees one
    # page, and counting that page said 20 for any search longer than one.
    from accounts.models import School
    here = School.objects.create(region=region, code='HERE', name='Here University', email_domain='here.edu.tw')
    there = School.objects.create(region=region, code='THERE', name='There University', email_domain='there.edu.tw')
    for i in range(25):
        book = Book.objects.create(title=f"Zyxqv {i}", isbn13=f"97800001{i:05d}", region=region)
        Listing.objects.create(
            seller=test_user, book=book, condition="new", price=100, status="active",
            school=here if i < 22 else there, region=region, currency=region.currency,
        )

    with mock.patch("search.views.search_google_books", return_value=[]):
        data = api.get("/api/v1/search/books/?q=Zyxqv&school=HERE").json()
    assert data["count"] == 25
    assert len(data["results"]) == 20
    assert data["local_count"] == 22

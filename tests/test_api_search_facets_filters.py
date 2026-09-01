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

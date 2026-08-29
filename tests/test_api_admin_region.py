import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.models import School, RegionVerification
from catalog.models import Book
from core.models import Region, Currency, Language
from listings.models import Listing
from orders.models import Order
from accounts.services import issue_tokens

User = get_user_model()
PASSWORD = "test-only-password-123"

def _auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}

@pytest.fixture
def api():
    return Client()

@pytest.fixture
def setup_data(db):
    twd, _ = Currency.objects.get_or_create(code="TWD", defaults={'symbol': "NT$", 'decimal_places': 0})
    hkd, _ = Currency.objects.get_or_create(code="HKD", defaults={'symbol': "HK$", 'decimal_places': 2})
    lang, _ = Language.objects.get_or_create(code="zh-TW", defaults={'name': "Traditional Chinese"})
    
    tw, _ = Region.objects.get_or_create(code="TW", defaults={'name': "Taiwan", 'currency': twd, 'default_language': lang})
    hk, _ = Region.objects.get_or_create(code="HK", defaults={'name': "Hong Kong", 'currency': hkd, 'default_language': lang})
    
    tw_school = School.objects.create(region=tw, email_domain="ntu.edu.tw", name="NTU")
    hk_school = School.objects.create(region=hk, email_domain="hku.hk", name="HKU")
    
    tw_book = Book.objects.create(region=tw, title="TW Book")
    hk_book = Book.objects.create(region=hk, title="HK Book")
    
    tw_seller = User.objects.create_user(email="tw-seller@test.com", password=PASSWORD, first_name="TW", last_name="Seller")
    RegionVerification.objects.create(user=tw_seller, region=tw, school=tw_school, edu_email="a@ntu.edu.tw", verified_at=timezone.now())
    
    hk_seller = User.objects.create_user(email="hk-seller@test.com", password=PASSWORD, first_name="HK", last_name="Seller")
    RegionVerification.objects.create(user=hk_seller, region=hk, school=hk_school, edu_email="a@hku.hk", verified_at=timezone.now())
    
    tw_listing = Listing.objects.create(region=tw, currency=twd, book=tw_book, seller=tw_seller, price=100)
    hk_listing = Listing.objects.create(region=hk, currency=hkd, book=hk_book, seller=hk_seller, price=10000)
    
    return {
        'tw': tw, 'hk': hk,
        'tw_school': tw_school, 'hk_school': hk_school,
        'tw_listing': tw_listing, 'hk_listing': hk_listing,
        'tw_seller': tw_seller, 'hk_seller': hk_seller
    }

@pytest.fixture
def tw_admin(db, setup_data):
    u = User.objects.create_user(email="tw-admin@test.com", password=PASSWORD, is_staff=True, first_name="TW", last_name="Admin")
    u.managed_regions.add(setup_data['tw'])
    return u

@pytest.fixture
def super_admin(db):
    u = User.objects.create_user(email="super-admin@test.com", password=PASSWORD, is_staff=True, is_superuser=True, first_name="Super", last_name="Admin")
    return u

def test_tw_admin_cannot_access_hk_data(api, tw_admin, setup_data):
    headers = _auth_header(tw_admin)
    
    # Listings list - should not see HK
    resp = api.get("/api/v1/admin/listings/", **headers)
    assert resp.status_code == 200
    ids = [item['id'] for item in resp.json()['results']]
    assert str(setup_data['tw_listing'].id) in ids
    assert str(setup_data['hk_listing'].id) not in ids
    
    # Listings detail - cannot read HK
    resp = api.get(f"/api/v1/admin/listings/{setup_data['hk_listing'].id}/", **headers)
    assert resp.status_code == 404
    
    # Users list - should see tw_seller but not hk_seller
    resp = api.get("/api/v1/admin/users/", **headers)
    assert resp.status_code == 200
    emails = [item['email'] for item in resp.json()['results']]
    assert setup_data['tw_seller'].email in emails
    assert setup_data['hk_seller'].email not in emails
    
    # Schools list - should see TW school but not HK school
    resp = api.get("/api/v1/admin/schools/", **headers)
    assert resp.status_code == 200
    ids = [item['id'] for item in resp.json()['results']]
    assert setup_data['tw_school'].id in ids
    assert setup_data['hk_school'].id not in ids

def test_super_admin_can_access_all(api, super_admin, setup_data):
    headers = _auth_header(super_admin)
    
    resp = api.get("/api/v1/admin/listings/", **headers)
    assert resp.status_code == 200
    ids = [item['id'] for item in resp.json()['results']]
    assert str(setup_data['tw_listing'].id) in ids
    assert str(setup_data['hk_listing'].id) in ids

def test_tw_admin_cannot_modify_managed_regions(api, tw_admin, super_admin, setup_data):
    # tw_admin tries to give themselves HK
    headers = _auth_header(tw_admin)
    resp = api.post(f"/api/v1/admin/managers/{tw_admin.id}/toggle/", 
        {"managed_regions": ["TW", "HK"]}, content_type="application/json", **headers)
    assert resp.status_code == 403
    
    tw_admin.refresh_from_db()
    assert "HK" not in tw_admin.managed_regions.values_list('code', flat=True)

    # super_admin CAN modify managed_regions
    super_headers = _auth_header(super_admin)
    resp = api.post(f"/api/v1/admin/managers/{tw_admin.id}/toggle/", 
        {"managed_regions": ["TW", "HK"]}, content_type="application/json", **super_headers)
    assert resp.status_code == 200
    
    tw_admin.refresh_from_db()
    assert "HK" in tw_admin.managed_regions.values_list('code', flat=True)

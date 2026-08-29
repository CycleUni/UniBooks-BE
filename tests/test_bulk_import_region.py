import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from accounts.models import School
from core.models import Category, Region, Currency, Language
from accounts.services import issue_tokens

User = get_user_model()

@pytest.fixture
def setup_regions(db):
    twd, _ = Currency.objects.get_or_create(code="TWD", defaults={'symbol': "NT$", 'decimal_places': 0})
    hkd, _ = Currency.objects.get_or_create(code="HKD", defaults={'symbol': "HK$", 'decimal_places': 2})
    lang, _ = Language.objects.get_or_create(code="zh-TW", defaults={'name': "Traditional Chinese"})
    
    tw, _ = Region.objects.get_or_create(code="TW", defaults={'name': "Taiwan", 'currency': twd, 'default_language': lang})
    hk, _ = Region.objects.get_or_create(code="HK", defaults={'name': "Hong Kong", 'currency': hkd, 'default_language': lang})
    return tw, hk

@pytest.fixture
def superuser(setup_regions):
    return User.objects.create_superuser(email="admin@test.com", password="pwd", first_name="A", last_name="B")

@pytest.fixture
def staff_tw(setup_regions):
    tw, hk = setup_regions
    user = User.objects.create_user(email="staff@test.com", password="pwd", first_name="A", last_name="B", is_staff=True)
    user.managed_regions.add(tw)
    return user

@pytest.fixture
def api():
    return Client()

def _auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}

def test_superuser_cross_region_blocked(api, superuser, setup_regions):
    res = api.post('/api/v1/admin/schools/bulk/?region=tw', {
        'action': 'apply',
        'items': [{'email_domain': 'hku.hk', 'name': 'HKU', 'region': 'HK'}]
    }, content_type='application/json', **_auth(superuser))
    
    assert res.status_code == 403
    assert res.json()['error']['code'] == 'admin.errAllRegionsForbidden'
    assert not School.objects.filter(email_domain='hku.hk').exists()
    
    res = api.post('/api/v1/admin/categories/bulk/?region=tw', {
        'action': 'apply',
        'items': [{'slug': 'eng', 'title': 'Eng', 'region': 'HK'}]
    }, content_type='application/json', **_auth(superuser))
    assert res.status_code == 403
    assert not Category.objects.filter(slug='eng').exists()

def test_missing_region_assumes_current(api, superuser, setup_regions):
    tw, hk = setup_regions
    res = api.post('/api/v1/admin/schools/bulk/?region=tw', {
        'action': 'apply',
        'items': [{'email_domain': 'ntu.edu.tw', 'name': 'NTU'}]
    }, content_type='application/json', **_auth(superuser))
    
    assert res.status_code == 200
    assert len(res.json()['new']) == 1
    assert School.objects.get(email_domain='ntu.edu.tw').region_id == 'TW'
    
    res = api.post('/api/v1/admin/categories/bulk/?region=tw', {
        'action': 'apply',
        'items': [{'slug': 'cs', 'title': 'CS'}]
    }, content_type='application/json', **_auth(superuser))
    assert res.status_code == 200
    assert Category.objects.get(slug='cs').region_id == 'TW'

def test_non_superuser_wrong_region(api, staff_tw, setup_regions):
    res = api.post('/api/v1/admin/schools/bulk/?region=hk', {
        'action': 'apply',
        'items': [{'email_domain': 'hku.hk', 'name': 'HKU'}]
    }, content_type='application/json', **_auth(staff_tw))
    
    assert res.status_code == 403
    assert res.json()['error']['code'] == 'admin.errRegionForbidden'

def test_cross_region_steal_blocked(api, superuser, setup_regions):
    tw, hk = setup_regions
    School.objects.create(email_domain='ntu.edu.tw', name='NTU', region=tw)
    
    res = api.post('/api/v1/admin/schools/bulk/?region=hk', {
        'action': 'apply',
        'items': [{'email_domain': 'ntu.edu.tw', 'name': 'NTU HK'}]
    }, content_type='application/json', **_auth(superuser))
    
    assert res.status_code == 403
    assert res.json()['error']['code'] == 'admin.errAllRegionsForbidden'
    assert School.objects.get(email_domain='ntu.edu.tw').region_id == 'TW'
    assert School.objects.get(email_domain='ntu.edu.tw').name == 'NTU'

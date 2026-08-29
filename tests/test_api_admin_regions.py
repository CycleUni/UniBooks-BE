import pytest
from django.urls import reverse
from rest_framework import status
from core.models import Region, Currency, Language
from accounts.models import User
from rest_framework.test import APIClient

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def superuser_client(api_client, db):
    user, _ = User.objects.get_or_create(email='super@example.com', defaults={'first_name': 'A', 'last_name': 'B'})
    user.is_superuser = True
    user.set_password('pw')
    user.save()
    api_client.force_authenticate(user=user)
    return api_client

@pytest.fixture
def normal_client(api_client, db):
    user, _ = User.objects.get_or_create(email='test@example.com', defaults={'first_name': 'C', 'last_name': 'D'})
    user.is_superuser = False
    user.set_password('pw')
    user.save()
    api_client.force_authenticate(user=user)
    return api_client

@pytest.fixture
def base_data(db):
    c, _ = Currency.objects.get_or_create(code='USD', defaults={'symbol': '$', 'decimal_places': 2})
    l1, _ = Language.objects.get_or_create(code='en', defaults={'name': 'English'})
    l2, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name': 'Chinese'})
    return c, l1, l2

@pytest.mark.django_db
def test_admin_region_permission(normal_client, base_data):
    url = reverse('admin-region-list')
    resp = normal_client.post(url, {})
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    c, l1, l2 = base_data
    r, _ = Region.objects.get_or_create(code='US', defaults={'name': 'USA', 'currency': c, 'default_language': l1})
    r.languages.set([l1])
    url_detail = reverse('admin-region-detail', kwargs={'pk': r.code})
    resp = normal_client.patch(url_detail, {}, format='json')
    assert resp.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.django_db
def test_admin_region_default_language_validation(superuser_client, base_data):
    url = reverse('admin-region-list')
    c, l1, l2 = base_data
    data = {
        'code': 'JP',
        'name': 'Japan',
        'currency': c.code,
        'default_language': l1.code,
        'languages': [l2.code],
        'translations': {'zh-TW': {'name': '日本'}},
        'timezone': 'Asia/Tokyo',
    }
    resp = superuser_client.post(url, data, format='json')
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert 'default_language' in resp.json()

@pytest.mark.django_db
def test_admin_region_disable_last_active(superuser_client, base_data):
    c, l1, l2 = base_data
    Region.objects.all().delete()
    r = Region.objects.create(code='US', name='USA', currency=c, default_language=l1, is_active=True)
    r.languages.set([l1])
    
    url = reverse('admin-region-detail', kwargs={'pk': r.code})
    resp = superuser_client.patch(url, {'is_active': False}, format='json')
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert 'is_active' in resp.json()

@pytest.mark.django_db
def test_admin_region_create_cache_invalidation(superuser_client, api_client, base_data):
    c, l1, l2 = base_data
    url = reverse('admin-region-list')
    data = {
        'code': 'JP',
        'name': 'Japan',
        'currency': c.code,
        'default_language': l1.code,
        'languages': [l1.code],
        'translations': {'en': {'name': 'Japan'}},
        'timezone': 'Asia/Tokyo',
        'is_active': True,
    }
    
    core_url = '/api/v1/core/regions/'
    resp1 = api_client.get(core_url)
    assert 'JP' not in [r['code'] for r in resp1.json()]
    
    resp_create = superuser_client.post(url, data, format='json')
    assert resp_create.status_code == status.HTTP_201_CREATED
    
    resp2 = api_client.get(core_url)
    assert 'JP' in [r['code'] for r in resp2.json()]

@pytest.mark.django_db
def test_admin_currency_decimal_places_locked(superuser_client, base_data):
    """Changing decimal_places reinterprets every stored amount at once, since
    money is held in minor units. This used to return 200 with the field
    silently dropped, which told the caller it had worked."""
    c, _, _ = base_data
    url = reverse('admin-currency-detail', kwargs={'pk': c.code})
    resp = superuser_client.patch(url, {'decimal_places': 0}, format='json')
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    c.refresh_from_db()
    assert c.decimal_places == 2

    # Sending the value it already has is not a change, so a client that
    # PATCHes the whole object back unchanged must not be rejected.
    resp = superuser_client.patch(url, {'decimal_places': 2, 'symbol': 'HK$$'}, format='json')
    assert resp.status_code == status.HTTP_200_OK
    c.refresh_from_db()
    assert c.symbol == 'HK$$'  

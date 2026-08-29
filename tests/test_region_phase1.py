import pytest
from django.test import RequestFactory
from django.core.cache import cache
from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from decimal import Decimal
from django.core.exceptions import ValidationError

from core.models import Currency, Language, Region
from core.i18n import normalize_language, resolve_language
from core.region import get_region
from core.money import to_minor, from_minor, validate_minor

class DummyRegionView(APIView):
    def get(self, request):
        region = get_region(request)
        has_req_region = hasattr(request, 'region')
        return Response({"region_code": region.code if region else None, "has_req_region": has_req_region})

urlpatterns = [
    path('test-region/', DummyRegionView.as_view()),
]

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def setup_regions(db):
    cache.clear()
    twd, _ = Currency.objects.get_or_create(code='TWD', defaults={'symbol': 'NT$', 'decimal_places': 0})
    hkd, _ = Currency.objects.get_or_create(code='HKD', defaults={'symbol': 'HK$', 'decimal_places': 2})
    
    en, _ = Language.objects.get_or_create(code='en', defaults={'name': 'English'})
    zh_tw, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name': 'Traditional Chinese (Taiwan)'})
    zh_hk, _ = Language.objects.get_or_create(code='zh-HK', defaults={'name': 'Traditional Chinese (Hong Kong)'})
    
    tw, _ = Region.objects.get_or_create(code='TW', defaults={'name': 'Taiwan', 'currency': twd, 'default_language': zh_tw, 'is_active': True})
    tw.languages.set([zh_tw, en])
    
    hk, _ = Region.objects.get_or_create(code='HK', defaults={'name': 'Hong Kong', 'currency': hkd, 'default_language': zh_hk, 'is_active': True})
    hk.languages.set([zh_hk, en])
    
    return tw, hk

@pytest.mark.django_db
def test_language_resolution(setup_regions):
    tw, hk = setup_regions
    
    assert normalize_language('zh-hant', tw) == 'zh-TW'
    assert normalize_language('zh-HK', tw) == 'zh-HK'
    assert normalize_language('zh-TW', tw) == 'zh-TW'
    assert normalize_language('yue', tw) == 'zh-HK'
    assert normalize_language('zh-CN', tw) == 'zh-TW'
    assert normalize_language('unknown-lang', tw) == 'unknown-lang'

    assert normalize_language('zh-hant', hk) == 'zh-HK'
    assert normalize_language('zh-HK', hk) == 'zh-HK'
    assert normalize_language('zh-TW', hk) == 'zh-HK'
    assert normalize_language('yue', hk) == 'zh-HK'
    assert normalize_language('zh-CN', hk) == 'zh-HK'
    assert normalize_language('unknown-lang', hk) == 'unknown-lang'

@pytest.mark.django_db
def test_resolve_language_request(setup_regions, rf):
    tw, hk = setup_regions
    
    req = rf.get('/')
    req._cached_region = tw
    assert resolve_language(req) == 'en'
    
    req = rf.get('/?lang=en')
    req._cached_region = tw
    assert resolve_language(req) == 'en'
    
    req = rf.get('/?lang=zh-HK')
    req._cached_region = tw
    assert resolve_language(req) == 'zh-TW'
    
    req = rf.get('/?lang=zh-HK')
    req._cached_region = hk
    assert resolve_language(req) == 'zh-HK'
    
    req = rf.get('/?lang=zh-TW')
    req._cached_region = tw
    assert resolve_language(req) == 'zh-TW'

    req = rf.get('/?lang=zh-TW')
    req._cached_region = hk
    assert resolve_language(req) == 'zh-HK'

@pytest.mark.django_db
@pytest.mark.urls(__name__)
def test_resolve_region_real_stack(setup_regions, api_client, monkeypatch):
    tw, hk = setup_regions
    
    # 1. CF-IPCountry only
    response = api_client.get('/test-region/', HTTP_CF_IPCOUNTRY='HK')
    assert response.data['region_code'] == 'HK'
    
    # 2. X-Region overrides CF-IPCountry
    response = api_client.get('/test-region/', HTTP_X_REGION='TW', HTTP_CF_IPCOUNTRY='HK')
    assert response.data['region_code'] == 'TW'
    
    # 3. User region testing with DRF stack
    from accounts.models import User
    user = User.objects.create(email='test@hk.edu')
    
    # Monkeypatch User.verified_regions for this test since Phase 2 doesn't exist yet
    class MockVerifiedRegions:
        def all(self):
            return [hk]
    monkeypatch.setattr(User, 'verified_regions', property(lambda self: MockVerifiedRegions()), raising=False)
    
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
    
    # Logged-in user's HK region overrides CF-IPCountry=TW
    response = api_client.get('/test-region/', HTTP_CF_IPCOUNTRY='TW')
    assert response.data['region_code'] == 'HK'
    assert response.data['has_req_region'] is False  # Prevents request.region trap

@pytest.mark.django_db
def test_money_conversions(setup_regions):
    # test to_minor with truncation/rounding behavior
    assert to_minor(Decimal('10.50'), 'HKD') == 1050
    assert to_minor(Decimal('10.005'), 'HKD') == 1001  # Rounds half up
    assert to_minor(Decimal('10.004'), 'HKD') == 1000

    assert to_minor(100, 'TWD') == 100
    assert to_minor(100.5, 'TWD') == 101

    # test from_minor
    assert from_minor(1050, 'HKD') == Decimal('10.50')
    assert from_minor(100, 'TWD') == Decimal('100')

    # test validate_minor
    validate_minor(1050, 'HKD')
    validate_minor(0, 'TWD')

    with pytest.raises(ValidationError):
        validate_minor(-5, 'HKD')
        
    with pytest.raises(ValidationError):
        validate_minor(10.5, 'HKD')
        
    with pytest.raises(ValidationError):
        validate_minor(True, 'HKD')  # Bool shouldn't pass
        
    with pytest.raises(ValidationError):
        validate_minor(100, 'INVALID')  # Currency does not exist

@pytest.mark.django_db
def test_currency_cache_invalidation(setup_regions):
    assert to_minor(Decimal('10.50'), 'HKD') == 1050
    
    # Update decimal places and ensure cache is cleared
    hkd = Currency.objects.get(code='HKD')
    hkd.decimal_places = 3
    hkd.save()
    
    assert to_minor(Decimal('10.50'), 'HKD') == 10500

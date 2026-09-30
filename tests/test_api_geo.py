"""GET /api/v1/core/geo/ — the region the visitor's IP places them in."""

import pytest
from django.core.cache import cache
from django.test import Client

from core.models import Currency, Language, Region

URL = "/api/v1/core/geo/"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def regions(db):
    cache.clear()
    twd, _ = Currency.objects.get_or_create(code='TWD', defaults={'symbol': 'NT$', 'decimal_places': 0})
    hkd, _ = Currency.objects.get_or_create(code='HKD', defaults={'symbol': 'HK$', 'decimal_places': 2})
    zh_tw, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name': 'Traditional Chinese (Taiwan)'})
    zh_hk, _ = Language.objects.get_or_create(code='zh-HK', defaults={'name': 'Traditional Chinese (Hong Kong)'})
    Region.objects.get_or_create(code='TW', defaults={'name': 'Taiwan', 'currency': twd, 'default_language': zh_tw, 'is_active': True})
    Region.objects.get_or_create(code='HK', defaults={'name': 'Hong Kong', 'currency': hkd, 'default_language': zh_hk, 'is_active': True})
    yield
    cache.clear()


def test_active_region_from_cloudflare_header(api, regions):
    resp = api.get(URL, HTTP_CF_IPCOUNTRY='hk')
    assert resp.status_code == 200
    assert resp.json() == {"region": "HK"}


def test_country_without_a_region_is_null(api, regions):
    assert api.get(URL, HTTP_CF_IPCOUNTRY='US').json() == {"region": None}
    # Cloudflare's pseudo-codes for "unknown" and Tor.
    assert api.get(URL, HTTP_CF_IPCOUNTRY='XX').json() == {"region": None}
    assert api.get(URL, HTTP_CF_IPCOUNTRY='T1').json() == {"region": None}


def test_missing_header_is_null(api, regions):
    """No CDN in front, or its proxy switched off: detection is skipped, not an error."""
    resp = api.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"region": None}


def test_ignores_region_the_frontend_already_sent(api, regions):
    """?region= and cookies outrank the IP in get_region(); here only the IP counts."""
    api.cookies['region'] = 'TW'
    resp = api.get(URL + "?region=TW", HTTP_CF_IPCOUNTRY='HK', HTTP_X_REGION='TW')
    assert resp.json() == {"region": "HK"}


def test_is_never_cached(api, regions):
    resp = api.get(URL, HTTP_CF_IPCOUNTRY='HK')
    assert resp['Cache-Control'] == 'no-store'


def test_header_name_follows_settings(api, regions, settings):
    settings.GEOIP_COUNTRY_HEADER = 'CloudFront-Viewer-Country'
    assert api.get(URL, HTTP_CF_IPCOUNTRY='HK').json() == {"region": None}
    assert api.get(URL, HTTP_CLOUDFRONT_VIEWER_COUNTRY='HK').json() == {"region": "HK"}

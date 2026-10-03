"""Schools grouped by city, and the fall back to the city when a school has no books.

NTU and NTNU are both in Taipei; only NTU has a book. NCKU (Tainan) has none
and nobody else in Tainan does either, so it gets no fallback. NTUST has no
city at all. A Hong Kong school with a book checks nothing crosses regions.
"""
import json

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import RegionVerification, School, User
from accounts.services import issue_tokens
from catalog.models import Book
from core.default_cities import DEFAULT_CITIES, seed_default_cities
from core.models import City, Region
from listings.models import Listing

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    tw, hk = Region.objects.get(code='TW'), Region.objects.get(code='HK')
    tpe = City.objects.get(region=tw, code='TPE')
    tnn = City.objects.get(region=tw, code='TNN')
    hki = City.objects.get(region=hk, code='HKI')

    ntu = School.objects.create(region=tw, code='NTU', name='NTU', email_domain='ntu.edu.tw', city=tpe)
    ntnu = School.objects.create(region=tw, code='NTNU', name='NTNU', email_domain='ntnu.edu.tw', city=tpe)
    ncku = School.objects.create(region=tw, code='NCKU', name='NCKU', email_domain='ncku.edu.tw', city=tnn)
    ntust = School.objects.create(region=tw, code='NTUST', name='NTUST', email_domain='ntust.edu.tw')
    hku = School.objects.create(region=hk, code='HKU', name='HKU', email_domain='hku.hk', city=hki)

    def listing(region, school, title, isbn, category=None):
        seller, _ = User.objects.get_or_create(
            email=f'seller@{school.email_domain}', defaults={'first_name': 'S', 'last_name': region.code})
        RegionVerification.objects.get_or_create(
            user=seller, region=region,
            defaults={'school': school, 'edu_email': seller.email, 'verified_at': timezone.now(), 'is_active': True},
        )
        book = Book.objects.create(title=title, isbn13=isbn, region=region, source='manual')
        return Listing.objects.create(
            seller=seller, book=book, price=100, condition='new', region=region,
            currency=region.currency, school=school, category=category,
        )

    from core.models import Category
    category = Category.objects.filter(region=tw).first()
    listing(tw, ntu, 'Taipei Calculus', '9780000007001', category)
    listing(hk, hku, 'Pok Fu Lam Law', '9780000007002')
    return {'tw': tw, 'hk': hk, 'ntu': ntu, 'ntnu': ntnu, 'ncku': ncku, 'ntust': ntust,
            'hku': hku, 'category': category}


def _recent(code, region='TW'):
    return APIClient().get(f'/api/v1/listings/recent_books/?school={code}', HTTP_X_REGION=region).json()


# --- seeding --------------------------------------------------------------

def test_default_cities_are_seeded_once_per_region(world):
    assert City.objects.filter(region=world['tw']).count() == len(DEFAULT_CITIES['TW']) == 22
    assert list(City.objects.filter(region=world['hk']).values_list('code', flat=True)) == ['HKI', 'KLN', 'NT']
    # A region that already has cities is left alone.
    assert seed_default_cities(world['tw']) == 0


# --- metadata -------------------------------------------------------------

def test_metadata_lists_cities_and_each_schools_city(world):
    data = APIClient().get('/api/v1/core/metadata/?lang=zh-TW', HTTP_X_REGION='TW').json()
    by_code = {s['code']: s for s in data['schools']}
    assert by_code['NTU']['city'] == 'TPE'
    assert by_code['NTUST']['city'] is None
    assert data['cities'][0] == {'code': 'TPE', 'name': 'Taipei City', 'display_name': '臺北市'}
    assert [c['code'] for c in data['cities']][:3] == ['TPE', 'NWT', 'KEE']


def test_metadata_ignores_a_payload_cached_before_cities(world):
    from django.core.cache import cache
    cache.set('home_static_TW_en', {'schools': [{'id': 1, 'code': 'X', 'name': 'Stale'}], 'categories': []})
    data = APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW').json()
    assert 'X' not in {s['code'] for s in data['schools']}
    assert data['cities']


def test_renaming_a_city_reaches_the_metadata(world):
    APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    tpe = City.objects.get(region=world['tw'], code='TPE')
    tpe.name = 'Taipei'
    tpe.save()
    data = APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW').json()
    assert data['cities'][0]['name'] == 'Taipei'


# --- home: recent books ---------------------------------------------------

def test_school_with_books_shows_its_own(world):
    data = _recent('NTU')
    assert data['scope'] == 'school'
    assert data['city'] == 'TPE'
    assert [b['title'] for b in data['results']] == ['Taipei Calculus']


def test_school_without_books_falls_back_to_its_city(world):
    data = _recent('NTNU')
    assert data['scope'] == 'city'
    assert data['city'] == 'TPE'
    assert [b['title'] for b in data['results']] == ['Taipei Calculus']
    # Prices and conditions are the city's copies, not the empty school's.
    assert data['results'][0]['min_price'] == 100
    assert data['results'][0]['conditions'] == {'new': 1}


def test_no_fallback_when_the_city_is_empty_too(world):
    data = _recent('NCKU')
    assert data['scope'] == 'school'
    assert data['results'] == []


def test_no_fallback_for_a_school_without_a_city(world):
    data = _recent('NTUST')
    assert data['scope'] == 'school'
    assert data['city'] is None
    assert data['results'] == []


def test_all_schools_is_unchanged(world):
    data = _recent('')
    assert data['scope'] == 'all'
    assert [b['title'] for b in data['results']] == ['Taipei Calculus']


def test_fallback_stays_inside_the_region(world):
    # Another Hong Kong Island school with nothing: falls back to HKU only.
    hk_other = School.objects.create(region=world['hk'], code='HKAPA', name='HKAPA', email_domain='hkapa.edu',
                                     city=world['hku'].city)
    data = _recent(hk_other.code, region='HK')
    assert data['scope'] == 'city'
    assert [b['title'] for b in data['results']] == ['Pok Fu Lam Law']


# --- search ---------------------------------------------------------------

def _search(params, region='TW'):
    return APIClient().get('/api/v1/search/books/', params, HTTP_X_REGION=region).json()


def test_category_browse_falls_back_to_the_city(world):
    slug = world['category'].slug
    own = _search({'category': slug, 'school': 'NTU'})
    assert own['scope'] == 'school'
    assert [r['title'] for r in own['results']] == ['Taipei Calculus']

    fallback = _search({'category': slug, 'school': 'NTNU'})
    assert fallback['scope'] == 'city'
    assert fallback['city'] == 'TPE'
    assert [r['title'] for r in fallback['results']] == ['Taipei Calculus']
    # "Local" now means the city, so the shown book counts as local.
    assert fallback['local_count'] == 1

    empty = _search({'category': slug, 'school': 'NCKU'})
    assert empty['scope'] == 'school'
    assert empty['results'] == []


def test_keyword_search_counts_copies_in_the_city(world, monkeypatch):
    # Local results only: no external catalogue in a test.
    import search.views
    monkeypatch.setattr(search.views, 'lookup_in_order', lambda *a, **k: ([], None, {}, False))
    data = _search({'q': 'Taipei Calculus', 'school': 'NTNU'})
    assert data['scope'] == 'school'
    assert data['local_count'] == 0
    assert data['city'] == 'TPE'
    assert data['city_count'] == 1
    item = next(r for r in data['results'] if r['title'] == 'Taipei Calculus')
    assert item['localActiveListings'] == 0
    assert item['cityActiveListings'] == 1

    tainan = _search({'q': 'Taipei Calculus', 'school': 'NCKU'})
    assert tainan['city_count'] == 0


# --- admin ----------------------------------------------------------------

@pytest.fixture
def superuser():
    return User.objects.create_superuser(email='root@example.com', password='x', first_name='R', last_name='T')


def _auth(user):
    return {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(user)['access']}"}


def _send(method, url, payload, user):
    return getattr(APIClient(), method)(url, data=json.dumps(payload), content_type='application/json', **_auth(user))


def test_admin_sets_and_clears_a_schools_city(world, superuser):
    url = f"/api/v1/admin/schools/{world['ntust'].id}/"
    resp = _send('patch', url, {'city': 'tpe'}, superuser)
    assert resp.status_code == 200, resp.content
    assert resp.json()['city'] == 'TPE'
    world['ntust'].refresh_from_db()
    assert world['ntust'].city.code == 'TPE'

    resp = _send('patch', url, {'city': None}, superuser)
    assert resp.status_code == 200, resp.content
    assert resp.json()['city'] is None


def test_admin_rejects_another_regions_city(world, superuser):
    resp = _send('patch', f"/api/v1/admin/schools/{world['ntust'].id}/", {'city': 'HKI'}, superuser)
    assert resp.status_code == 400
    assert resp.json() == {'city': ['admin.errSchoolCityUnknown']}


def test_bulk_import_sets_city_and_rejects_unknown_codes(world, superuser):
    url = '/api/v1/admin/schools/bulk/?region=TW'
    resp = _send('post', url, {'action': 'apply', 'items': [
        {'name': 'NTNU', 'email_domain': 'ntnu.edu.tw', 'region': 'TW', 'code': 'NTNU', 'city': 'TNN'},
        {'name': 'NTPU', 'email_domain': 'ntpu.edu.tw', 'region': 'TW', 'city': 'NWT'},
    ]}, superuser)
    assert resp.status_code == 200, resp.content
    assert len(resp.json()['modified']) == 1
    assert School.objects.get(email_domain='ntnu.edu.tw').city.code == 'TNN'
    assert School.objects.get(email_domain='ntpu.edu.tw').city.code == 'NWT'

    # Leaving `city` out keeps the school's own.
    resp = _send('post', url, {'action': 'preview', 'items': [
        {'name': 'NTNU', 'email_domain': 'ntnu.edu.tw', 'region': 'TW', 'code': 'NTNU'},
    ]}, superuser)
    assert len(resp.json()['unchanged']) == 1

    # One unknown city writes nothing at all.
    resp = _send('post', url, {'action': 'apply', 'items': [
        {'name': 'NTU renamed', 'email_domain': 'ntu.edu.tw', 'region': 'TW', 'city': 'TNN'},
        {'name': 'Bad', 'email_domain': 'bad.edu.tw', 'region': 'TW', 'city': 'HKI'},
    ]}, superuser)
    assert resp.status_code == 400
    assert resp.json()['error'] == {'code': 'admin.errSchoolCityUnknown', 'domains': ['bad.edu.tw']}
    assert School.objects.get(email_domain='ntu.edu.tw').name == 'NTU'
    assert not School.objects.filter(email_domain='bad.edu.tw').exists()

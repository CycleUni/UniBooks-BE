"""Searching schools in any language.

A school's Chinese name lives in `translations`, not `name`; every search
that looked only at `name` found nothing for 臺灣大學.
"""
import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import RegionVerification, School, User
from accounts.services import issue_tokens
from catalog.models import Book
from core.models import Region
from listings.models import Listing

pytestmark = pytest.mark.django_db


@pytest.fixture
def ntu():
    tw = Region.objects.get(code='TW')
    school = School.objects.create(
        region=tw, code='NTU', name='National Taiwan University', email_domain='ntu.edu.tw',
        translations={'zh-TW': {'name': '國立臺灣大學'}},
    )
    other = School.objects.create(
        region=tw, code='NCCU', name='National Chengchi University', email_domain='nccu.edu.tw',
        translations={'zh-TW': {'name': '國立政治大學'}},
    )
    for s, isbn in ((school, '9780000007001'), (other, '9780000007002')):
        seller = User.objects.create_user(email=f'seller@{s.email_domain}', password='x', first_name='S', last_name='T')
        RegionVerification.objects.create(
            user=seller, region=tw, school=s, edu_email=seller.email, verified_at=timezone.now(), is_active=True,
        )
        book = Book.objects.create(title=f'Book {s.code}', isbn13=isbn, region=tw, source='manual')
        Listing.objects.create(
            seller=seller, book=book, price=100, condition='new', region=tw, currency=tw.currency, school=s,
        )
    return school


@pytest.fixture
def auth():
    root = User.objects.create_superuser(email='root@example.com', password='x', first_name='R', last_name='T')
    return {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(root)['access']}"}


@pytest.mark.parametrize('q', ['臺灣大學', 'Taiwan University', 'ntu.edu', 'NTU'])
def test_admin_school_list_matches_every_name(ntu, auth, q):
    resp = APIClient().get('/api/v1/admin/schools/', {'region': 'TW', 'q': q}, **auth)
    assert [s['code'] for s in resp.json()['results']] == ['NTU']


def test_admin_school_list_does_not_match_translation_keys(ntu, auth):
    resp = APIClient().get('/api/v1/admin/schools/', {'region': 'TW', 'q': 'zh-TW'}, **auth)
    assert resp.json()['results'] == []


def test_admin_listings_match_the_schools_chinese_name(ntu, auth):
    resp = APIClient().get('/api/v1/admin/listings/', {'region': 'TW', 'q': '臺灣大學'}, **auth)
    assert [r['book']['title'] for r in resp.json()['results']] == ['Book NTU']


def test_stats_school_breakdown_matches_names_in_other_languages(ntu, auth):
    for q in ('國立臺灣大學', 'national taiwan', 'ntu'):
        resp = APIClient().get('/api/v1/admin/stats/breakdown/', {'region': 'TW', 'by': 'school', 'lang': 'en', 'q': q}, **auth)
        assert [r['id'] for r in resp.json()['results']] == [ntu.id], q
    assert resp.json()['results'][0]['keywords'] == ['National Taiwan University', '國立臺灣大學', 'NTU']


def test_metadata_lists_every_name_of_a_school(ntu):
    cache.clear()
    resp = APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    school = next(s for s in resp.json()['schools'] if s['code'] == 'NTU')
    assert school['display_name'] == 'National Taiwan University'
    assert school['names'] == ['National Taiwan University', '國立臺灣大學']


def test_metadata_ignores_a_school_list_cached_without_names(ntu):
    cache.set('home_static_TW_en', {
        'schools': [{'id': ntu.id, 'code': 'NTU', 'name': ntu.name}],
        'cities': [],
        'categories': [],
    })
    resp = APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    assert all('names' in s for s in resp.json()['schools'])

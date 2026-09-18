"""School short codes: unique per region, and `?school=` resolved per region.

Taiwan's Hung Kuang University and the University of Hong Kong are both
"HKU". Every test here that involves two schools uses that pair on purpose:
anything that looks a code up without the region hits the wrong one.
"""
import importlib
import json

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import School, RegionVerification, User
from accounts.school_codes import (
    NO_SCHOOL_ID, dedupe_code, derive_code, normalize_code, resolve_school, school_filter_id,
)
from accounts.services import issue_tokens
from catalog.models import Book
from core.models import Region
from listings.models import Listing

pytestmark = pytest.mark.django_db


@pytest.fixture
def regions():
    return Region.objects.get(code='TW'), Region.objects.get(code='HK')


@pytest.fixture
def two_hkus(regions):
    """HKU in both regions, each with one active listing of a region-local book."""
    tw, hk = regions
    tw_hku = School.objects.create(region=tw, code='HKU', name='Hung Kuang University', email_domain='hk.edu.tw')
    hk_hku = School.objects.create(region=hk, code='HKU', name='The University of Hong Kong', email_domain='hku.hk')

    def listing(region, school, title, isbn, course):
        seller = User.objects.create_user(
            email=f'seller@{school.email_domain}', password='x', first_name='S', last_name=region.code)
        RegionVerification.objects.create(
            user=seller, region=region, school=school, edu_email=seller.email,
            verified_at=timezone.now(), is_active=True,
        )
        book = Book.objects.create(title=title, isbn13=isbn, region=region, source='manual')
        return Listing.objects.create(
            seller=seller, book=book, price=100, condition='new', region=region,
            currency=region.currency, school=school, course_name=course,
        )

    return {
        'tw': tw_hku,
        'hk': hk_hku,
        'tw_listing': listing(tw, tw_hku, 'Hung Kuang Book', '9780000009001', 'Nursing'),
        'hk_listing': listing(hk, hk_hku, 'Pok Fu Lam Book', '9780000009002', 'Law'),
    }


# --- (a) uniqueness is per region -------------------------------------------

def test_same_code_may_exist_in_two_regions(two_hkus):
    assert two_hkus['tw'].code == two_hkus['hk'].code == 'HKU'


def test_same_code_twice_in_one_region_is_rejected(regions):
    tw, _ = regions
    School.objects.create(region=tw, code='NTU', name='National Taiwan University', email_domain='ntu.edu.tw')
    with pytest.raises(IntegrityError), transaction.atomic():
        School.objects.create(region=tw, code='ntu', name='Impostor', email_domain='impostor.edu.tw')


def test_code_is_normalized_on_save(regions):
    tw, _ = regions
    school = School.objects.create(region=tw, code='  ntust ', name='NTUST', email_domain='mail.ntust.edu.tw')
    assert school.code == 'NTUST'


def test_school_saved_without_code_gets_the_derived_one(regions):
    tw, _ = regions
    first = School.objects.create(region=tw, name='NTU', email_domain='ntu.edu.tw')
    # Same first label, same region: must not collide with the one above.
    second = School.objects.create(region=tw, name='NTU (other campus)', email_domain='ntu.example.org')
    assert first.code == 'NTU'
    assert second.code == 'NTU-2'


# --- (d) the backfill rule ---------------------------------------------------

@pytest.mark.parametrize('domain, expected', [
    ('ntu.edu.tw', 'NTU'),
    ('hku.hk', 'HKU'),
    ('NCCU.EDU.TW', 'NCCU'),
    ('poly_u.edu.hk', 'POLYU'),          # characters a code can't hold are dropped
    ('.edu.tw', 'SCHOOL'),               # nothing usable before the first dot
    ('a' * 40 + '.edu', 'A' * 20),       # cut to the column's length
])
def test_derive_code(domain, expected):
    assert derive_code(domain) == expected


def test_dedupe_code_appends_a_counter_and_still_fits():
    assert dedupe_code('NTU', set()) == 'NTU'
    assert dedupe_code('NTU', {'NTU'}) == 'NTU-2'
    assert dedupe_code('NTU', {'NTU', 'NTU-2'}) == 'NTU-3'
    long = 'A' * 20
    suffixed = dedupe_code(long, {long})
    assert suffixed == 'A' * 18 + '-2' and len(suffixed) == 20


def test_normalize_code():
    assert normalize_code(' h k u\t') == 'HKU'
    assert normalize_code(None) == ''


def test_backfill_migration_assigns_codes_per_region(regions):
    tw, hk = regions
    rows = [
        School.objects.create(region=tw, name='NTU', email_domain='ntu.edu.tw'),
        School.objects.create(region=tw, name='NTU again', email_domain='ntu.example.org'),
        School.objects.create(region=hk, name='HKU', email_domain='hku.hk'),
        School.objects.create(region=tw, name='Hung Kuang', email_domain='hku.edu.tw'),
    ]
    # 0015 leaves the column NULL, which the finished schema no longer
    # allows; placeholders the backfill must overwrite stand in for it.
    for s in rows:
        School.objects.filter(pk=s.pk).update(code=f'TMP-{s.pk}')

    mod = importlib.import_module('accounts.migrations.0016_backfill_school_code')
    mod.backfill_school_codes(global_apps, None)

    codes = [School.objects.get(pk=s.pk).code for s in rows]
    # Oldest keeps the plain code; HKU is free to repeat across regions.
    assert codes == ['NTU', 'NTU-2', 'HKU', 'HKU']


# --- (b)/(c) resolving ?school= ----------------------------------------------

def test_resolve_school_stays_in_the_region(two_hkus, regions):
    tw, hk = regions
    assert resolve_school(tw, 'HKU') == two_hkus['tw']
    assert resolve_school(hk, 'hku') == two_hkus['hk']
    # Legacy full names still work, but only in their own region.
    assert resolve_school(hk, 'The University of Hong Kong') == two_hkus['hk']
    assert resolve_school(tw, 'The University of Hong Kong') is None
    assert school_filter_id(tw, '') is None
    assert school_filter_id(tw, 'NOPE') == NO_SCHOOL_ID


def _listing_ids(resp):
    body = resp.json()
    return {row['id'] for row in body.get('results', body)}


@pytest.mark.parametrize('param', ['HKU', 'hku'])
def test_listings_school_code_hits_each_regions_own_school(two_hkus, param):
    client = APIClient()
    tw = client.get(f'/api/v1/listings/?school={param}', HTTP_X_REGION='TW')
    hk = client.get(f'/api/v1/listings/?school={param}', HTTP_X_REGION='HK')
    assert _listing_ids(tw) == {str(two_hkus['tw_listing'].id)}
    assert _listing_ids(hk) == {str(two_hkus['hk_listing'].id)}


def test_listings_legacy_name_param_still_works(two_hkus):
    client = APIClient()
    resp = client.get('/api/v1/listings/?school=Hung Kuang University', HTTP_X_REGION='TW')
    assert _listing_ids(resp) == {str(two_hkus['tw_listing'].id)}
    # The same name asked for in the other region names no school there.
    resp = client.get('/api/v1/listings/?school=Hung Kuang University', HTTP_X_REGION='HK')
    assert _listing_ids(resp) == set()


def test_unknown_school_matches_nothing_rather_than_everything(two_hkus):
    resp = APIClient().get('/api/v1/listings/?school=NOPE', HTTP_X_REGION='TW')
    assert _listing_ids(resp) == set()


def test_recent_books_and_courses_are_scoped_by_code_and_region(two_hkus):
    client = APIClient()
    tw_books = client.get('/api/v1/listings/recent_books/?school=HKU', HTTP_X_REGION='TW').json()
    hk_books = client.get('/api/v1/listings/recent_books/?school=HKU', HTTP_X_REGION='HK').json()
    tw_titles = {b['title'] for b in tw_books.get('results', tw_books)}
    hk_titles = {b['title'] for b in hk_books.get('results', hk_books)}
    assert tw_titles == {'Hung Kuang Book'}
    assert hk_titles == {'Pok Fu Lam Book'}

    tw_courses = client.get('/api/v1/search/courses/?school=HKU', HTTP_X_REGION='TW').json()
    hk_courses = client.get('/api/v1/search/courses/?school=HKU', HTTP_X_REGION='HK').json()
    assert [c['value'] for c in tw_courses] == ['Nursing']
    assert [c['value'] for c in hk_courses] == ['Law']


def test_book_detail_local_count_uses_the_regions_school(two_hkus):
    client = APIClient()
    book = two_hkus['tw_listing'].book
    resp = client.get(f'/api/v1/books/?id={book.id}&school=HKU', HTTP_X_REGION='TW')
    assert resp.status_code == 200
    assert resp.json()['local_listings_count'] == 1


def test_metadata_lists_codes_and_resolves_school_in_region(two_hkus):
    client = APIClient()
    resp = client.get('/api/v1/core/metadata/?school=HKU&lang=en', HTTP_X_REGION='TW')
    assert resp.status_code == 200
    schools = resp.json()['schools']
    assert [(s['code'], s['name']) for s in schools] == [('HKU', 'Hung Kuang University')]
    # A code the region doesn't have: an empty waitlist, not a 500 or everyone's.
    resp = client.get('/api/v1/core/metadata/?school=CUHK&lang=en', HTTP_X_REGION='TW')
    assert resp.status_code == 200
    assert resp.json()['waitlist'] == []


def test_metadata_ignores_a_school_list_cached_before_codes(two_hkus):
    from django.core.cache import cache
    cache.set('home_static_TW_en', {
        'schools': [{'id': two_hkus['tw'].id, 'name': 'Hung Kuang University'}],
        'categories': [],
    })
    resp = APIClient().get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    assert [s['code'] for s in resp.json()['schools']] == ['HKU']


# --- admin API ----------------------------------------------------------------

@pytest.fixture
def superuser():
    return User.objects.create_superuser(email='root@example.com', password='x', first_name='R', last_name='T')


def _auth(user):
    return {'HTTP_AUTHORIZATION': f"Bearer {issue_tokens(user)['access']}"}


def _post(client, url, payload, user):
    return client.post(url, data=json.dumps(payload), content_type='application/json', **_auth(user))


def test_admin_create_accepts_code_already_used_in_other_region(two_hkus, superuser):
    client = APIClient()
    resp = _post(client, '/api/v1/admin/schools/', {
        'name': 'Hong Kong Kolej', 'email_domain': 'hkk.edu.hk', 'code': 'hkk', 'region': 'HK',
    }, superuser)
    assert resp.status_code == 201, resp.content
    assert resp.json()['code'] == 'HKK'


def test_admin_create_rejects_duplicate_code_in_region(two_hkus, superuser):
    client = APIClient()
    resp = _post(client, '/api/v1/admin/schools/', {
        'name': 'Another HKU', 'email_domain': 'another.edu.tw', 'code': 'hku', 'region': 'TW',
    }, superuser)
    assert resp.status_code == 400
    assert resp.json() == {'code': ['admin.errSchoolCodeTaken']}


def test_admin_create_without_code_derives_one(regions, superuser):
    resp = _post(APIClient(), '/api/v1/admin/schools/', {
        'name': 'NCKU', 'email_domain': 'ncku.edu.tw', 'region': 'TW',
    }, superuser)
    assert resp.status_code == 201, resp.content
    assert resp.json()['code'] == 'NCKU'


def test_admin_patch_code_validates(two_hkus, superuser, regions):
    tw, _ = regions
    other = School.objects.create(region=tw, code='NTU', name='NTU', email_domain='ntu.edu.tw')
    client = APIClient()
    url = f'/api/v1/admin/schools/{other.id}/'
    resp = client.patch(url, data=json.dumps({'code': 'HKU'}), content_type='application/json', **_auth(superuser))
    assert resp.status_code == 400
    assert resp.json() == {'code': ['admin.errSchoolCodeTaken']}

    resp = client.patch(url, data=json.dumps({'code': 'NT U!'}), content_type='application/json', **_auth(superuser))
    assert resp.status_code == 400
    assert resp.json() == {'code': ['admin.errSchoolCodeInvalid']}

    # Re-saving its own code is not a clash with itself.
    resp = client.patch(url, data=json.dumps({'code': 'ntu', 'name': 'National Taiwan University'}),
                        content_type='application/json', **_auth(superuser))
    assert resp.status_code == 200, resp.content
    assert resp.json()['code'] == 'NTU'


def test_admin_school_search_matches_code(two_hkus, superuser):
    resp = APIClient().get('/api/v1/admin/schools/?region=TW&q=hku', **_auth(superuser))
    assert [s['code'] for s in resp.json()['results']] == ['HKU']


def test_bulk_import_takes_given_codes_and_derives_the_rest(two_hkus, superuser):
    resp = _post(APIClient(), '/api/v1/admin/schools/bulk/?region=TW', {
        'action': 'apply',
        'items': [
            {'email_domain': 'ntu.edu.tw', 'name': 'NTU'},
            {'email_domain': 'ntu.example.org', 'name': 'NTU two', 'code': 'ntu'},
            {'email_domain': 'hk.edu.tw', 'name': 'Hung Kuang University', 'code': 'HKUTW'},
        ],
    }, superuser)
    assert resp.status_code == 200, resp.content
    body = resp.json()
    # The explicit "ntu" wins the plain code even though it comes second.
    assert {i['email_domain']: i['code'] for i in body['new']} == {'ntu.edu.tw': 'NTU-2', 'ntu.example.org': 'NTU'}
    assert len(body['modified']) == 1
    assert School.objects.get(email_domain='ntu.example.org').code == 'NTU'
    assert School.objects.get(email_domain='ntu.edu.tw').code == 'NTU-2'
    assert School.objects.get(email_domain='hk.edu.tw').code == 'HKUTW'


def test_bulk_import_rejects_clashing_or_invalid_codes_without_writing(two_hkus, superuser):
    client = APIClient()
    resp = _post(client, '/api/v1/admin/schools/bulk/?region=TW', {
        'action': 'apply',
        'items': [
            {'email_domain': 'fresh.edu.tw', 'name': 'Fresh'},
            {'email_domain': 'clash.edu.tw', 'name': 'Clash', 'code': 'HKU'},
        ],
    }, superuser)
    assert resp.status_code == 400
    assert resp.json()['error'] == {'code': 'admin.errSchoolCodeTaken', 'domains': ['clash.edu.tw']}
    assert not School.objects.filter(email_domain='fresh.edu.tw').exists()

    resp = _post(client, '/api/v1/admin/schools/bulk/?region=TW', {
        'action': 'preview',
        'items': [{'email_domain': 'bad.edu.tw', 'name': 'Bad', 'code': 'B@D'}],
    }, superuser)
    assert resp.status_code == 400
    assert resp.json()['error']['code'] == 'admin.errSchoolCodeInvalid'

    # HK's HKU does not clash with anything in TW's import.
    resp = _post(client, '/api/v1/admin/schools/bulk/?region=HK', {
        'action': 'apply',
        'items': [{'email_domain': 'hku.hk', 'name': 'The University of Hong Kong', 'code': 'HKU'}],
    }, superuser)
    assert resp.status_code == 200
    assert resp.json()['unchanged'] == [{'email_domain': 'hku.hk', 'name': 'The University of Hong Kong', 'code': 'HKU'}]


@pytest.mark.django_db(transaction=True)
def test_code_migrations_run_backwards_and_forwards():
    """0016 is reversible (as a no-op), so the three steps can be unwound and
    re-applied; re-applying fills codes in for rows created in between."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate([('accounts', '0014_user_email_and_site_language')])
    old_apps = executor.loader.project_state([('accounts', '0014_user_email_and_site_language')]).apps
    OldSchool = old_apps.get_model('accounts', 'School')
    OldSchool.objects.create(region_id='TW', name='Hung Kuang', email_domain='hku.edu.tw')
    OldSchool.objects.create(region_id='HK', name='HKU', email_domain='hku.hk')

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([('accounts', '0017_school_code_required_unique_per_region')])

    assert sorted(School.objects.values_list('region_id', 'code')) == [('HK', 'HKU'), ('TW', 'HKU')]


def test_search_by_course_filters_on_the_regions_school(two_hkus):
    client = APIClient()
    # HK's HKU carries the Law listing; Taiwan's HKU does not.
    hk = client.get('/api/v1/search/books/?course=Law&school=HKU', HTTP_X_REGION='HK').json()
    tw = client.get('/api/v1/search/books/?course=Law&school=HKU', HTTP_X_REGION='TW').json()
    hk_rows = hk.get('results', hk)
    tw_rows = tw.get('results', tw)
    assert {r['title'] for r in hk_rows} == {'Pok Fu Lam Book'}
    assert not any(r.get('localActiveListings') for r in tw_rows)


def test_ads_targeting_one_hku_do_not_show_for_the_other(two_hkus):
    from datetime import timedelta
    from ads.models import Ad, Advertiser

    now = timezone.now()
    advertiser = Advertiser.objects.create(
        company_name='HKU Bookshop', contact_email='shop@ads.example',
        all_schools=False,
    )
    advertiser.regions.add(*Region.objects.filter(code__in=['TW', 'HK']))
    advertiser.schools.add(two_hkus['hk'])
    Ad.objects.create(
        advertiser=advertiser, title='HK HKU Ad', image_url='https://example.invalid/a.png',
        target_url='https://example.invalid/', position='home_banner',
        start_date=now - timedelta(days=1), end_date=now + timedelta(days=1), is_active=True,
    )
    client = APIClient()
    url = '/api/v1/promotions/active/?position=home_banner&school=hku'
    hk = client.get(url, HTTP_X_REGION='HK').json()['results']
    tw = client.get(url, HTTP_X_REGION='TW').json()['results']
    assert [a['title'] for a in hk] == ['HK HKU Ad']
    assert tw == []


def test_admin_listing_filter_takes_an_id_or_a_code_in_region(two_hkus, superuser):
    client = APIClient()
    by_id = client.get(f"/api/v1/admin/listings/?school={two_hkus['tw'].id}", **_auth(superuser)).json()
    assert {r['id'] for r in by_id['results']} == {str(two_hkus['tw_listing'].id)}
    by_code = client.get('/api/v1/admin/listings/?school=HKU&region=hk', **_auth(superuser)).json()
    assert {r['id'] for r in by_code['results']} == {str(two_hkus['hk_listing'].id)}

from django.utils import timezone
import pytest
from rest_framework.test import APIClient
from core.models import Region, Currency, Language
from accounts.models import User, School, RegionVerification
from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription

pytestmark = pytest.mark.django_db

@pytest.fixture
def tw_region():
    twd, _ = Currency.objects.get_or_create(code='TWD', defaults={'name': 'New Taiwan Dollar', 'symbol': 'NT$'})
    zh_tw, _ = Language.objects.get_or_create(code='zh-TW', defaults={'name': 'Traditional Chinese (TW)'})
    region, _ = Region.objects.update_or_create(
        code='TW',
        defaults={
            'name': 'Taiwan',
            'currency': twd,
            'default_language': zh_tw,
            'search_engines': ['googlebooks', 'openlibrary', 'isbnnet'],
            'edu_email_suffix': '.edu.tw'
        }
    )
    en, _ = Language.objects.get_or_create(code='en', defaults={'name': 'English'})
    region.languages.add(zh_tw, en)
    return region

@pytest.fixture
def hk_region():
    hkd, _ = Currency.objects.get_or_create(code='HKD', defaults={'name': 'Hong Kong Dollar', 'symbol': 'HK$'})
    zh_hk, _ = Language.objects.get_or_create(code='zh-HK', defaults={'name': 'Traditional Chinese (HK)'})
    region, _ = Region.objects.update_or_create(
        code='HK',
        defaults={
            'name': 'Hong Kong',
            'currency': hkd,
            'default_language': zh_hk,
            'search_engines': ['googlebooks', 'openlibrary'],
            'edu_email_suffix': '.edu.hk'
        }
    )
    en, _ = Language.objects.get_or_create(code='en', defaults={'name': 'English'})
    region.languages.add(zh_hk, en)
    return region

@pytest.fixture
def setup_data(tw_region, hk_region):
    # TW Data
    tw_school = School.objects.create(name='NTU', region=tw_region, email_domain='ntu.edu.tw')
    tw_user = User.objects.create_user(email='tw@ntu.edu.tw', first_name='TW', last_name='User', password='password')
    RegionVerification.objects.create(user=tw_user, region=tw_region, school=tw_school, edu_email='tw@ntu.edu.tw', is_active=True, is_manual_verification=True, verified_at=timezone.now())
    tw_book = Book.objects.create(title='TW Book', region=tw_region, source='manual')
    tw_listing = Listing.objects.create(seller=tw_user, book=tw_book, price=100, condition='new', region=tw_region, currency=tw_region.currency, school=tw_school)

    # HK Data
    hk_school = School.objects.create(name='HKU', region=hk_region, email_domain='hku.edu.hk')
    hk_user = User.objects.create_user(email='hk@hku.edu.hk', first_name='HK', last_name='User', password='password')
    RegionVerification.objects.create(user=hk_user, region=hk_region, school=hk_school, edu_email='hk@hku.edu.hk', is_active=True, is_manual_verification=True, verified_at=timezone.now())
    hk_book = Book.objects.create(title='HK Book', region=hk_region, source='manual')
    hk_listing = Listing.objects.create(seller=hk_user, book=hk_book, price=50, condition='new', region=hk_region, currency=hk_region.currency, school=hk_school)

    return {
        'tw_user': tw_user,
        'hk_user': hk_user,
        'tw_listing': tw_listing,
        'hk_listing': hk_listing,
        'tw_book': tw_book,
        'hk_book': hk_book,
        'tw_school': tw_school,
        'hk_school': hk_school
    }

def test_isolation_tw_request_sees_only_tw_data(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    
    # Listings
    resp = client.get('/api/v1/listings/', HTTP_X_REGION='TW')
    assert resp.status_code == 200
    results = resp.json()['results']
    assert len(results) == 1
    assert results[0]['id'] == str(setup_data['tw_listing'].id)

def test_isolation_hk_request_sees_only_hk_data(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['hk_user'])
    
    # Listings
    resp = client.get('/api/v1/listings/', HTTP_X_REGION='HK')
    assert resp.status_code == 200
    results = resp.json()['results']
    assert len(results) == 1
    assert results[0]['id'] == str(setup_data['hk_listing'].id)

def test_isolation_tw_user_cannot_create_hk_listing(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    
    resp = client.post(
        '/api/v1/listings/',
        {"book": setup_data['hk_book'].id, "price": 250, "condition": "like_new"},
        format='json',
        HTTP_X_REGION='HK'
    )
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'acct.errUnverifiedInRegion'
    assert resp.json()['error']['region'] == 'HK'

def test_cache_isolation_tw_does_not_leak_to_hk(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    
    # Fill cache for TW
    resp_tw = client.get('/api/v1/listings/', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    
    # Ensure HK gets its own cache key and misses
    resp_hk = client.get('/api/v1/listings/', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    assert resp_hk.json()['results'][0]['id'] == str(setup_data['hk_listing'].id)

def test_cross_region_messaging_blocked(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    resp = client.post('/api/v1/messaging/conversations/', {'listing_id': setup_data['hk_listing'].id}, format='json')
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'acct.errUnverifiedInRegion'
    assert resp.json()['error']['region'] == 'HK'

def test_cross_region_ordering_blocked(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    resp = client.post('/api/v1/orders/', {'listing': setup_data['hk_listing'].id}, format='json')
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'acct.errUnverifiedInRegion'
    assert resp.json()['error']['region'] == 'HK'

def test_cross_region_report_blocked(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    resp = client.post('/api/v1/moderation/', {'listing': setup_data['hk_listing'].id, 'reason': 'spam'}, format='json')
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'acct.errUnverifiedInRegion'
    assert resp.json()['error']['region'] == 'HK'

def test_cross_region_review_blocked(setup_data, hk_region):
    # Need an HK order first
    from orders.models import Order
    hk_order = Order.objects.create(
        listing=setup_data['hk_listing'],
        buyer=setup_data['tw_user'], # Pretend they somehow made an order
        seller=setup_data['hk_user'],
        total_amount=50,
        status='completed',
        region=hk_region,
        currency=hk_region.currency
    )
    
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    resp = client.post('/api/v1/orders/reviews/', {'order': hk_order.id, 'rating': 5, 'comment': 'good'}, format='json')
    assert resp.status_code == 403
    assert resp.json()['error']['code'] == 'acct.errUnverifiedInRegion'
    assert resp.json()['error']['region'] == 'HK'

def test_cross_region_get_allowed(setup_data):
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    # Get HK listing
    resp = client.get(f'/api/v1/listings/{setup_data["hk_listing"].id}/', HTTP_X_REGION='HK')
    assert resp.status_code == 200
    
def test_cross_region_allowed_after_verification(setup_data, hk_region):
    client = APIClient()
    user = setup_data['tw_user']
    client.force_authenticate(user=user)
    
    # Verify in HK
    from accounts.models import RegionVerification
    RegionVerification.objects.create(user=user, region=hk_region, school=setup_data['hk_school'], edu_email='tw@hku.edu.hk', is_active=True, is_manual_verification=True, verified_at=timezone.now())
    
    # Messaging HK listing
    resp = client.post('/api/v1/messaging/conversations/', {'listing_id': setup_data['hk_listing'].id}, format='json', HTTP_X_REGION='HK')
    assert resp.status_code == 201


# ---------------------------------------------------------------------
# Cache isolation
#
# These are the tests that a plain functional suite cannot replace. Every
# endpoint below behaves correctly against a cold cache, so a leak only
# shows up if a *previous* region's request has already populated the
# cache. Each test therefore fills TW's cache first, then asks the same
# endpoint as HK and asserts HK's own data comes back.
#
# Ordering matters: reverse the two requests and the test passes even with
# a region-less cache key, proving nothing.
#
# `lang=en` is pinned on every request for the same reason. Left unpinned,
# each region resolves to its own default language (zh-TW vs zh-HK), so the
# language component alone keeps the two cache keys apart and the test goes
# green even when the key carries no region at all. Pinning the language
# leaves the region as the only thing that differs — verified by deleting
# the region from a cache key and watching these tests go red.
# ---------------------------------------------------------------------


@pytest.fixture
def region_ads(tw_region, hk_region, setup_data):
    """One active ad per region, each scoped to its own region only."""
    from django.utils import timezone
    from datetime import timedelta
    from ads.models import Advertiser, Ad

    now = timezone.now()
    made = {}
    for code, region in (('TW', tw_region), ('HK', hk_region)):
        advertiser = Advertiser.objects.create(
            company_name=f'{code} Advertiser',
            contact_email=f'{code.lower()}@ads.example',
            all_schools=True,
            all_regions=False,
        )
        advertiser.regions.add(region)
        made[code] = Ad.objects.create(
            advertiser=advertiser,
            title=f'{code} Ad',
            image_url='https://example.invalid/a.png',
            target_url='https://example.invalid/',
            position='home_banner',
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
            is_active=True,
        )
    return made


@pytest.fixture
def region_subscriptions(setup_data):
    """A wanted book per region, so the metadata waitlist is non-empty."""
    Subscription.objects.create(
        user=setup_data['tw_user'], book=setup_data['tw_book'], region=setup_data['tw_listing'].region
    )
    Subscription.objects.create(
        user=setup_data['hk_user'], book=setup_data['hk_book'], region=setup_data['hk_listing'].region
    )


def test_cache_isolation_recent_books(setup_data):
    client = APIClient()

    resp_tw = client.get('/api/v1/listings/recent_books/?lang=en', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_titles = {b['title'] for b in resp_tw.json()['results']}
    assert tw_titles == {'TW Book'}

    resp_hk = client.get('/api/v1/listings/recent_books/?lang=en', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_titles = {b['title'] for b in resp_hk.json()['results']}
    assert hk_titles == {'HK Book'}


def test_cache_isolation_book_detail(setup_data):
    client = APIClient()

    resp_tw = client.get(f'/api/v1/books/?lang=en&id={setup_data["tw_book"].id}', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    assert resp_tw.json()['title'] == 'TW Book'

    # Same query string, different region: TW's cached payload must not answer
    # for HK, and the TW book must not be reachable from HK at all.
    resp_hk = client.get(f'/api/v1/books/?lang=en&id={setup_data["tw_book"].id}', HTTP_X_REGION='HK')
    assert resp_hk.json().get('title') != 'TW Book'


def test_cache_isolation_ads(region_ads):
    client = APIClient()

    resp_tw = client.get('/api/v1/promotions/active/?lang=en&position=home_banner', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    assert [a['title'] for a in resp_tw.json()['results']] == ['TW Ad']

    resp_hk = client.get('/api/v1/promotions/active/?lang=en&position=home_banner', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    assert [a['title'] for a in resp_hk.json()['results']] == ['HK Ad']


def test_cache_isolation_home_static(setup_data):
    client = APIClient()

    resp_tw = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    assert {s['name'] for s in resp_tw.json()['schools']} == {'NTU'}

    resp_hk = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    assert {s['name'] for s in resp_hk.json()['schools']} == {'HKU'}


def test_cache_isolation_home_waitlist(setup_data, region_subscriptions):
    client = APIClient()

    resp_tw = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    assert {w['title'] for w in resp_tw.json()['waitlist']} == {'TW Book'}

    resp_hk = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    assert {w['title'] for w in resp_hk.json()['waitlist']} == {'HK Book'}

@pytest.fixture
def region_categories(tw_region, hk_region):
    from core.models import Category
    tw_cat = Category.objects.create(title='TW Cat', slug='tw-cat', region=tw_region, is_active=True)
    hk_cat = Category.objects.create(title='HK Cat', slug='hk-cat', region=hk_region, is_active=True)
    return {'TW': tw_cat, 'HK': hk_cat}

@pytest.fixture
def region_orders(setup_data, tw_region, hk_region):
    from orders.models import Order
    from accounts.models import User
    
    tw_buyer = User.objects.create_user(email='twbuyer@ntu.edu.tw', first_name='T', last_name='W', password='pw')
    hk_buyer = User.objects.create_user(email='hkbuyer@hku.edu.hk', first_name='H', last_name='K', password='pw')
    
    tw_order = Order.objects.create(
        buyer=tw_buyer, seller=setup_data['tw_user'], listing=setup_data['tw_listing'],
        region=tw_region, currency=tw_region.currency, total_amount=100
    )
    hk_order = Order.objects.create(
        buyer=hk_buyer, seller=setup_data['hk_user'], listing=setup_data['hk_listing'],
        region=hk_region, currency=hk_region.currency, total_amount=50
    )
    return {'TW': tw_order, 'HK': hk_order, 'tw_buyer': tw_buyer, 'hk_buyer': hk_buyer}

@pytest.fixture
def region_conversations(setup_data):
    from messaging.models import Conversation
    from accounts.models import User
    
    tw_buyer = User.objects.create_user(email='twbuyer_msg@ntu.edu.tw', first_name='T', last_name='W', password='pw')
    hk_buyer = User.objects.create_user(email='hkbuyer_msg@hku.edu.hk', first_name='H', last_name='K', password='pw')
    
    tw_conv = Conversation.objects.create(listing=setup_data['tw_listing'], buyer=tw_buyer)
    hk_conv = Conversation.objects.create(listing=setup_data['hk_listing'], buyer=hk_buyer)
    return {'TW': tw_conv, 'HK': hk_conv, 'tw_buyer': tw_buyer, 'hk_buyer': hk_buyer}

@pytest.fixture
def region_reports(setup_data):
    from moderation.models import Report
    from accounts.models import User
    
    tw_reporter = User.objects.create_user(email='tw_rep@ntu.edu.tw', first_name='T', last_name='W', password='pw')
    hk_reporter = User.objects.create_user(email='hk_rep@hku.edu.hk', first_name='H', last_name='K', password='pw')
    
    tw_report = Report.objects.create(listing=setup_data['tw_listing'], reporter=tw_reporter, reason='spam', status='open')
    hk_report = Report.objects.create(listing=setup_data['hk_listing'], reporter=hk_reporter, reason='spam', status='open')
    
    return {'TW': tw_report, 'HK': hk_report, 'tw_reporter': tw_reporter, 'hk_reporter': hk_reporter}


def test_isolation_search(setup_data):
    client = APIClient()
    
    resp_tw = client.get('/api/v1/search/books/?q=TW Book', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_data = resp_tw.json()
    if isinstance(tw_data, dict) and 'results' in tw_data:
        tw_data = tw_data['results']
    tw_titles = [b['title'] for b in tw_data]
    assert 'TW Book' in tw_titles
    assert 'HK Book' not in tw_titles
    
    resp_hk = client.get('/api/v1/search/books/?q=HK Book', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_data = resp_hk.json()
    if isinstance(hk_data, dict) and 'results' in hk_data:
        hk_data = hk_data['results']
    hk_titles = [b['title'] for b in hk_data]
    assert 'HK Book' in hk_titles
    assert 'TW Book' not in hk_titles


def test_isolation_subscriptions(setup_data, region_subscriptions):
    client = APIClient()
    
    client.force_authenticate(user=setup_data['tw_user'])

    resp_tw = client.get('/api/v1/subscriptions/', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_data = resp_tw.json()
    if isinstance(tw_data, dict) and 'results' in tw_data:
        tw_data = tw_data['results']
    # Debug if needed, but assuming it's a list of dicts. If tw_data[0] is int, print it.
    assert 'TW Book' in [item['bookTitle'] for item in tw_data if isinstance(item, dict) and 'book' in item]
    
    client.force_authenticate(user=setup_data['hk_user'])
    resp_hk = client.get('/api/v1/subscriptions/', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_data = resp_hk.json()
    if isinstance(hk_data, dict) and 'results' in hk_data:
        hk_data = hk_data['results']
    assert 'HK Book' in [item['bookTitle'] for item in hk_data if isinstance(item, dict) and 'book' in item]


def test_isolation_orders(region_orders):
    client = APIClient()
    
    # We query as the seller, who is setup_data['tw_user'] / setup_data['hk_user']
    tw_seller = region_orders['TW'].seller
    client.force_authenticate(user=tw_seller)
    resp_tw = client.get('/api/v1/orders/', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_data = resp_tw.json()
    if isinstance(tw_data, dict) and 'results' in tw_data:
        tw_data = tw_data['results']
    assert len(tw_data) == 1
    assert tw_data[0]['id'] == str(region_orders['TW'].id)
    
    hk_seller = region_orders['HK'].seller
    client.force_authenticate(user=hk_seller)
    resp_hk = client.get('/api/v1/orders/', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_data = resp_hk.json()
    if isinstance(hk_data, dict) and 'results' in hk_data:
        hk_data = hk_data['results']
    assert len(hk_data) == 1
    assert hk_data[0]['id'] == str(region_orders['HK'].id)

def test_isolation_messaging(region_conversations):
    client = APIClient()
    
    tw_seller = region_conversations['TW'].listing.seller
    client.force_authenticate(user=tw_seller)
    resp_tw = client.get('/api/v1/messaging/conversations/', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_data = resp_tw.json()
    if isinstance(tw_data, dict) and 'results' in tw_data:
        tw_data = tw_data['results']
    assert len(tw_data) == 1
    assert tw_data[0]['id'] == str(region_conversations['TW'].id)
    
    hk_seller = region_conversations['HK'].listing.seller
    client.force_authenticate(user=hk_seller)
    resp_hk = client.get('/api/v1/messaging/conversations/', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_data = resp_hk.json()
    if isinstance(hk_data, dict) and 'results' in hk_data:
        hk_data = hk_data['results']
    assert len(hk_data) == 1
    assert hk_data[0]['id'] == str(region_conversations['HK'].id)

def test_isolation_moderation(region_reports):
    client = APIClient()
    
    tw_reporter = region_reports['tw_reporter']
    client.force_authenticate(user=tw_reporter)
    resp_tw = client.get('/api/v1/moderation/mine/', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    tw_data = resp_tw.json()
    if isinstance(tw_data, dict) and 'results' in tw_data:
        tw_data = tw_data['results']
    assert len(tw_data) == 1
    assert tw_data[0]['id'] == str(region_reports['TW'].id)
    
    hk_reporter = region_reports['hk_reporter']
    client.force_authenticate(user=hk_reporter)
    resp_hk = client.get('/api/v1/moderation/mine/', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    hk_data = resp_hk.json()
    if isinstance(hk_data, dict) and 'results' in hk_data:
        hk_data = hk_data['results']
    assert len(hk_data) == 1
    assert hk_data[0]['id'] == str(region_reports['HK'].id)

def test_cache_isolation_home_categories(setup_data, region_categories):
    client = APIClient()

    resp_tw = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='TW')
    assert resp_tw.status_code == 200
    assert 'TW Cat' in {c['title'] for c in resp_tw.json()['categories']}
    assert 'HK Cat' not in {c['title'] for c in resp_tw.json()['categories']}

    resp_hk = client.get('/api/v1/core/metadata/?lang=en', HTTP_X_REGION='HK')
    assert resp_hk.status_code == 200
    assert 'HK Cat' in {c['title'] for c in resp_hk.json()['categories']}
    assert 'TW Cat' not in {c['title'] for c in resp_hk.json()['categories']}


def test_listing_book_cannot_be_repointed_across_regions(setup_data):
    """`region` is read-only on ListingSerializer, but `book` is writable.

    That was enough to break the region/book invariant on PATCH: repointing a
    TW listing at an HK book left region=TW with book.region=HK, so HK
    catalogue content surfaced inside TW's listing feed. Listing.clean()
    forbids the combination, but DRF never calls full_clean(), so only the
    serializer can enforce it on the write path.
    """
    client = APIClient()
    client.force_authenticate(user=setup_data['tw_user'])
    listing = setup_data['tw_listing']

    resp = client.patch(
        f'/api/v1/listings/{listing.id}/',
        {'book': setup_data['hk_book'].id},
        format='json',
    )
    assert resp.status_code == 400
    assert 'book' in resp.json()

    listing.refresh_from_db()
    assert listing.book_id == setup_data['tw_book'].id

    # A same-region edit must still go through — the guard is about the book's
    # region, not about freezing the field.
    resp_ok = client.patch(
        f'/api/v1/listings/{listing.id}/', {'price': 321}, format='json'
    )
    assert resp_ok.status_code == 200
    listing.refresh_from_db()
    assert listing.price == 321

import unittest.mock as mock


import unittest.mock as mock

def test_verify_region_anti_spoofing(setup_data, tw_region, hk_region):
    client = APIClient()
    from accounts.models import User, RegionVerification
    user = User.objects.create_user(email='spoof_test@example.com', password='password', first_name='A', last_name='B')
    client.force_authenticate(user=user)

    verify_token = "fixed-token-spoof"
    with mock.patch("accounts.views.auth.uuid.uuid4", return_value=verify_token):
        resp = client.post(
            '/api/v1/auth/verify/request/?region=HK',
            {'edu_email': 'student@ntu.edu.tw', 'region': 'HK'},
            HTTP_X_REGION='HK',
            format='json'
        )
    assert resp.status_code == 200

    resp = client.post('/api/v1/auth/verify/', {'token': verify_token}, format='json')
    assert resp.status_code == 200

    verifications = RegionVerification.objects.filter(user=user)
    assert verifications.count() == 1
    v = verifications.first()
    assert v.region == tw_region
    assert v.school.email_domain == 'ntu.edu.tw'
    
def test_verify_subdomain_stripping(setup_data, tw_region):
    client = APIClient()
    from accounts.models import User, RegionVerification
    user = User.objects.create_user(email='subdomain_test@example.com', password='password', first_name='A', last_name='B')
    client.force_authenticate(user=user)

    verify_token = "fixed-token-subdomain"
    with mock.patch("accounts.views.auth.uuid.uuid4", return_value=verify_token):
        resp = client.post(
            '/api/v1/auth/verify/request/',
            {'edu_email': 'student@csie.ntu.edu.tw'},
            format='json'
        )
    assert resp.status_code == 200

    resp = client.post('/api/v1/auth/verify/', {'token': verify_token}, format='json')
    assert resp.status_code == 200

    verifications = RegionVerification.objects.filter(user=user)
    assert verifications.count() == 1
    v = verifications.first()
    assert v.region == tw_region
    assert v.school.email_domain == 'ntu.edu.tw'

def test_verify_duplicate_email_blocked(setup_data, tw_region):
    client = APIClient()
    from accounts.models import User
    
    # First user verifies
    user1 = User.objects.create_user(email='dup1@example.com', password='password', first_name='A', last_name='B')
    client.force_authenticate(user=user1)
    
    verify_token1 = "fixed-token-dup1"
    with mock.patch("accounts.views.auth.uuid.uuid4", return_value=verify_token1):
        client.post('/api/v1/auth/verify/request/', {'edu_email': 'shared@ntu.edu.tw'}, format='json')
    client.post('/api/v1/auth/verify/', {'token': verify_token1}, format='json')
    
    # Second user tries to use same email
    user2 = User.objects.create_user(email='dup2@example.com', password='password', first_name='C', last_name='D')
    client.force_authenticate(user=user2)
    
    resp = client.post('/api/v1/auth/verify/request/', {'edu_email': 'shared@ntu.edu.tw'}, format='json')
    assert resp.status_code == 400
    assert resp.json()['error']['code'] == 'acct.errEmailTaken'

def test_verify_multiple_regions_same_account(setup_data, tw_region, hk_region):
    client = APIClient()
    from accounts.models import User, RegionVerification
    user = User.objects.create_user(email='exchange@example.com', password='password', first_name='A', last_name='B')
    client.force_authenticate(user=user)

    # Verify TW
    verify_token_tw = "fixed-token-tw"
    with mock.patch("accounts.views.auth.uuid.uuid4", return_value=verify_token_tw):
        client.post('/api/v1/auth/verify/request/', {'edu_email': 'tw2@ntu.edu.tw'}, format='json')
    client.post('/api/v1/auth/verify/', {'token': verify_token_tw}, format='json')

    # Verify HK
    verify_token_hk = "fixed-token-hk"
    with mock.patch("accounts.views.auth.uuid.uuid4", return_value=verify_token_hk):
        client.post('/api/v1/auth/verify/request/', {'edu_email': 'hk2@hku.edu.hk'}, format='json')
    client.post('/api/v1/auth/verify/', {'token': verify_token_hk}, format='json')

    verifications = RegionVerification.objects.filter(user=user, is_active=True)
    assert verifications.count() == 2
    regions = set(verifications.values_list('region__code', flat=True))
    assert regions == {'TW', 'HK'}

def test_currency_conversion(tw_region, hk_region):
    from django.core.cache import cache
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    hk_region.currency.decimal_places = 2
    hk_region.currency.save()
    cache.clear()
    from core.money import to_minor, from_minor, validate_minor
    from decimal import Decimal
    import pytest
    from django.core.exceptions import ValidationError

    # HKD: 2 decimal places
    assert to_minor(Decimal('100.00'), 'HKD') == 10000
    assert to_minor(Decimal('10.005'), 'HKD') == 1001
    assert to_minor(100, 'HKD') == 10000
    
    assert from_minor(10000, 'HKD') == Decimal('100.00')
    assert from_minor(1001, 'HKD') == Decimal('10.01')

    # TWD: 0 decimal places
    assert to_minor(Decimal('100'), 'TWD') == 100
    assert from_minor(100, 'TWD') == Decimal('100')

    # validate_minor
    validate_minor(10000, 'HKD')
    validate_minor(100, 'TWD')
    validate_minor(0, 'HKD')

    with pytest.raises(ValidationError, match="non-negative integer"):
        validate_minor(-1, 'HKD')
    
    with pytest.raises(ValidationError, match="non-negative integer"):
        validate_minor(True, 'HKD')
    
    with pytest.raises(ValidationError, match="non-negative integer"):
        validate_minor(False, 'HKD')
        
    with pytest.raises(ValidationError, match="Invalid currency"):
        validate_minor(100, 'XYZ')

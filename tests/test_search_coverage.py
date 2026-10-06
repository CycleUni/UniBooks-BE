"""What each search box finds: every field its placeholder names.

Each search used to match a different subset — the admin order search had no
ISBN, the admin listing search no author, the main search no category name.
"""
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import RegionVerification, School, User
from accounts.services import issue_tokens
from catalog.models import Book
from core.models import Category, Region
from listings.models import Listing
from orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    tw = Region.objects.get(code='TW')
    ntu = School.objects.create(
        region=tw, code='NTU', name='National Taiwan University', email_domain='ntu.edu.tw',
        translations={'zh-TW': {'name': '國立臺灣大學'}},
    )
    nccu = School.objects.create(
        region=tw, code='NCCU', name='National Chengchi University', email_domain='nccu.edu.tw',
        translations={'zh-TW': {'name': '國立政治大學'}},
    )
    category = Category.objects.create(
        region=tw, slug='econ-test', title='Economics Test',
        translations={'zh-TW': {'title': '經濟測試學院'}},
    )

    def user(email, school):
        u = User.objects.create_user(email=email, password='x', first_name=email.split('@')[0], last_name='T')
        RegionVerification.objects.create(
            user=u, region=tw, school=school, edu_email=email, verified_at=timezone.now(), is_active=True,
        )
        return u

    seller = user('seller@ntu.edu.tw', ntu)
    buyer = user('buyer@nccu.edu.tw', nccu)
    book = Book.objects.create(
        title='Principles of Zyxonomics', authors='Greg Mankiw', isbn13='9781305585126', region=tw, source='manual',
    )
    other = Book.objects.create(title='Unrelated', authors='Nobody', isbn13='9780000000017', region=tw, source='manual')
    listing = Listing.objects.create(
        seller=seller, book=book, price=100, condition='new', region=tw, currency=tw.currency, school=ntu,
        course_name='Intro Zyxonomics', professor_name='Prof Qwerty', category=category,
    )
    Listing.objects.create(seller=seller, book=other, price=100, condition='new', region=tw, currency=tw.currency, school=nccu)
    order = Order.objects.create(
        listing=listing, buyer=buyer, seller=seller, region=tw, currency=tw.currency, total_amount=100,
        book_title=book.title, book_isbn=book.isbn13,
    )
    root = User.objects.create_superuser(email='root@example.com', password='x', first_name='R', last_name='T')
    return {'book': book, 'order': order, 'seller': seller, 'buyer': buyer, 'root': root}


def _client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user)['access']}")
    return client


@pytest.mark.parametrize('q', ['Mankiw', '978-1-305-58512-6', 'Zyxonomics'])
def test_admin_orders_match_author_and_isbn_as_printed(world, q):
    body = _client(world['root']).get('/api/v1/admin/orders/', {'region': 'TW', 'q': q}).json()
    assert [o['id'] for o in body['results']] == [str(world['order'].id)]


@pytest.mark.parametrize('q', ['Mankiw', '978-1305585126', 'Intro Zyx', 'Qwerty', '臺灣大學'])
def test_admin_listings_match_author_isbn_course_professor_and_school(world, q):
    body = _client(world['root']).get('/api/v1/admin/listings/', {'region': 'TW', 'q': q}).json()
    assert [r['book']['title'] for r in body['results']] == ['Principles of Zyxonomics']


def test_admin_listings_show_the_school_in_the_page_language(world):
    body = _client(world['root']).get('/api/v1/admin/listings/', {'region': 'TW', 'q': 'Mankiw', 'lang': 'zh-TW'}).json()
    assert body['results'][0]['school']['display_name'] == '國立臺灣大學'


def test_admin_users_match_school_name_in_any_language(world):
    body = _client(world['root']).get('/api/v1/admin/users/', {'region': 'TW', 'q': '政治大學'}).json()
    assert [u['email'] for u in body['results']] == ['buyer@nccu.edu.tw']


def test_admin_users_lists_a_user_once_when_two_verifications_match(world):
    hk = Region.objects.get(code='HK')
    hku = School.objects.create(region=hk, code='HKU', name='The University of Hong Kong', email_domain='hku.hk')
    RegionVerification.objects.create(
        user=world['buyer'], region=hk, school=hku, edu_email='buyer@hku.hk', verified_at=timezone.now(), is_active=True,
    )
    body = _client(world['root']).get('/api/v1/admin/users/', {'q': 'buyer@'}).json()
    assert [u['email'] for u in body['results']] == ['buyer@nccu.edu.tw']


@pytest.mark.parametrize('q', ['Intro Zyx', 'Qwerty', '978-1305585126'])
def test_my_listings_match_course_professor_and_isbn_as_printed(world, q):
    body = _client(world['seller']).get('/api/v1/auth/me/', {'q': q}, HTTP_X_REGION='TW').json()
    assert [r['book_title'] for r in body['myListings']['results']] == ['Principles of Zyxonomics']


def test_order_carries_the_book_authors_for_the_account_search(world):
    body = _client(world['buyer']).get('/api/v1/orders/', HTTP_X_REGION='TW').json()
    orders = body['results'] if isinstance(body, dict) else body
    assert [o['book_authors'] for o in orders] == ['Greg Mankiw']


@pytest.mark.parametrize('q', ['經濟測試', 'Economics Test'])
def test_main_search_finds_books_listed_under_a_category_name(world, q):
    with mock.patch('search.views.search_google_books', return_value=[]), \
            mock.patch('search.views.search_open_library_books', return_value=[]):
        body = APIClient().get('/api/v1/search/books/', {'q': q}, HTTP_X_REGION='TW').json()
    assert [r['title'] for r in body['results']] == ['Principles of Zyxonomics']

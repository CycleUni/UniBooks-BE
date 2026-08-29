"""
200 distinct books, one active listing each — a catalogue-breadth fixture.

Distinct from both existing seed scripts: seed_books_and_listings.py stress-
tests one book with 2000 listings (many sellers of the same title);
seed_homepage_demo.py is a small, curated set shaped around specific UI
states. Neither exercises the "many different books" case — the home page's
recent-listings grid (and its own pager, default page size 200) never shows a
second page with either of them. This script is for that: enough distinct
books that Recently Added actually has something to paginate.

Run:  .venv/bin/python seed_bulk_books.py [region_code]
"""
import os
import random
import sys

import django
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cycleuni.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_bulk_books.py with DEBUG=False "
        "(this script creates fake users/books/listings and must never run against production)."
    )
    sys.exit(1)

from accounts.models import School, User, RegionVerification
from core.money import to_minor

# Seed prices are written in *major* units and converted with the region's
# currency. No per-region branch is needed: to_minor() is a no-op for a
# zero-decimal currency like TWD, so TW keeps its existing values byte for
# byte while HKD gets its two decimal places. A third region needs no edit
# here beyond a range.
SEED_PRICE_RANGE = {  # major units, per region code
    'TW': (100, 1000),
    'HK': (25, 250),
}


def seed_price(region, low_high=None):
    low, high = low_high or SEED_PRICE_RANGE.get(region.code, SEED_PRICE_RANGE['TW'])
    return to_minor(random.randint(low, high), region.currency.code)

from catalog.models import Book
from core.models import Category, Region
from listings.models import Listing

BOOK_COUNT = 200
SELLER_COUNT = 20
# 979-prefixed so these never collide with seed_homepage_demo.py's real
# ISBNs or seed_books_and_listings.py's 978-prefixed synthetic ones.
ISBN_PREFIX = '979'


def get_seller(tag, school, region):
    user, created = User.objects.get_or_create(
        email=f'bulkbook_{region.code.lower()}_{tag}@test.com',
        defaults={
            'first_name': f'BulkBook{region.code}{tag}',
            'last_name': 'User',
        },
    )
    if created:
        user.set_password('demopassword')
        user.save()
        
    RegionVerification.objects.update_or_create(
        user=user,
        region=region,
        defaults={
            'school': school,
            'edu_email': f'bulkbook_{region.code.lower()}_{tag}@{school.email_domain}',
            'verified_at': timezone.now(),
            'is_active': True,
        }
    )
    return user


def run(region_code='TW'):
    region = Region.objects.get(code=region_code)
    school = School.objects.filter(region=region).first()
    if school is None:
        print(f"WARNING: no School rows exist for region {region.code} — books/listings will have school=None,")
        print("         and the school filter on the home page will not match them.")
    category = Category.objects.filter(region=region).first()
    sellers = [get_seller(n, school, region) for n in range(SELLER_COUNT)]
    conditions = ['new', 'like_new', 'noted', 'damaged']

    books_created = 0
    listings_created = 0
    for i in range(BOOK_COUNT):
        prefix = '977' if region.code == 'HK' else ISBN_PREFIX
        isbn = f'{prefix}{i:010d}'
        book, was_created = Book.objects.get_or_create(
            isbn13=isbn,
            region=region,
            defaults={
                'title': f'Bulk Catalogue Book {i}',
                'authors': f'Author {i}',
                'publisher': 'Demo Press',
                'published_date': '2020',
                'source': 'preseed',
                # Left blank on purpose, same as seed_homepage_demo.py:
                # exercises the title-initial cover placeholder instead of
                # shipping fake cover art.
                'cover_url': '',
            },
        )
        books_created += int(was_created)

        _, listing_created = Listing.objects.get_or_create(
            book=book,
            seller=sellers[i % SELLER_COUNT],
            region=region,
            defaults={
                'currency': region.currency,
                'price': seed_price(region),
                'condition': random.choice(conditions),
                'category': category,
                'school': school,
                'status': 'active',
                'description': f'Bulk catalogue listing for {book.title}.',
            },
        )
        listings_created += int(listing_created)

    print(f"{books_created} new books created, {listings_created} new listings "
          f"created ({BOOK_COUNT} books targeted) for region {region.code}.")
    print("\nExpected on the home page:")
    print(f"  Recently added now has far more than one page's worth of books —")
    print(f"  the grid's pager should show a second page.")


if __name__ == '__main__':
    region_code = sys.argv[1] if len(sys.argv) > 1 else 'TW'
    run(region_code)

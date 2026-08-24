"""
200 distinct books, one active listing each — a catalogue-breadth fixture.

Distinct from both existing seed scripts: seed_books_and_listings.py stress-
tests one book with 2000 listings (many sellers of the same title);
seed_homepage_demo.py is a small, curated set shaped around specific UI
states. Neither exercises the "many different books" case — the home page's
recent-listings grid (and its own pager, default page size 200) never shows a
second page with either of them. This script is for that: enough distinct
books that Recently Added actually has something to paginate.

Run:  .venv/bin/python seed_bulk_books.py
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

from accounts.models import School, User
from catalog.models import Book
from core.models import Category
from listings.models import Listing

BOOK_COUNT = 200
SELLER_COUNT = 20
# 979-prefixed so these never collide with seed_homepage_demo.py's real
# ISBNs or seed_books_and_listings.py's 978-prefixed synthetic ones.
ISBN_PREFIX = '979'


def get_seller(tag, school):
    user, created = User.objects.get_or_create(
        email=f'bulkbook_{tag}@test.com',
        defaults={
            'first_name': f'BulkBook{tag}',
            'last_name': 'User',
            'edu_email': f'bulkbook_{tag}@ntu.edu.tw',
            'verified_at': timezone.now(),
            'school': school,
        },
    )
    if created:
        user.set_password('demopassword')
        user.save()
    return user


def run():
    school = School.objects.first()
    if school is None:
        print("WARNING: no School rows exist — books/listings will have school=None,")
        print("         and the school filter on the home page will not match them.")
    category = Category.objects.first()
    sellers = [get_seller(n, school) for n in range(SELLER_COUNT)]
    conditions = ['new', 'like_new', 'noted', 'damaged']

    books_created = 0
    listings_created = 0
    for i in range(BOOK_COUNT):
        isbn = f'{ISBN_PREFIX}{i:010d}'
        book, was_created = Book.objects.get_or_create(
            isbn13=isbn,
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
            defaults={
                'price': random.randint(100, 1000),
                'condition': random.choice(conditions),
                'category': category,
                'school': school,
                'status': 'active',
                'description': f'Bulk catalogue listing for {book.title}.',
            },
        )
        listings_created += int(listing_created)

    print(f"{books_created} new books created, {listings_created} new listings "
          f"created ({BOOK_COUNT} books targeted).")
    print("\nExpected on the home page:")
    print(f"  Recently added now has far more than one page's worth of books —")
    print(f"  the grid's pager should show a second page.")


if __name__ == '__main__':
    run()

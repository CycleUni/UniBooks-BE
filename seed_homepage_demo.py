"""
Home page demo data — small, realistic, and shaped around what the page
actually renders.

Distinct from seed_books_and_listings.py, which is a load fixture: one book,
2000 listings, all owned by a single seller. On the home page that renders as
"667 sellers" (a third of them survive the status='active' filter) for what is
really one person, which reads as fake data. This script instead aims at a
believable cold-start catalogue and at the specific states the UI has to cope
with: a free listing, a book nobody is selling yet, and covers that fail to
resolve.

Run:  .venv/bin/python seed_homepage_demo.py [region_code]
"""
import os
import random
import sys
import django
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_homepage_demo.py with DEBUG=False "
        "(this script creates fake users and listings)."
    )
    sys.exit(1)

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
from listings.models import Listing
from core.models import Category, Region
from accounts.models import User, School, RegionVerification
from subscriptions.models import Subscription

# (isbn13, title, authors, publisher)
BOOKS = [
    ('9780134685991', 'Effective Java',                            'Joshua Bloch',        'Addison-Wesley'),
    ('9781285741550', 'Calculus: Early Transcendentals',           'James Stewart',       'Cengage'),
    ('9780133594140', 'Computer Networking: A Top-Down Approach',  'Kurose & Ross',       'Pearson'),
    ('9781319050740', 'Principles of Economics',                   'N. Gregory Mankiw',   'Cengage'),
    ('9780321997845', 'Organic Chemistry',                         'Paula Bruice',        'Pearson'),
    ('9780073523408', 'Fundamentals of Thermodynamics',            'Borgnakke & Sonntag', 'Wiley'),
    # No listings on purpose. This book will NOT appear under "Recently added"
    # — that endpoint only returns books with an active listing — but it is
    # exactly what the "Most Wanted" waitlist is for: demand with no supply.
    ('9780262046305', 'Introduction to Algorithms',                'Cormen et al.',       'MIT Press'),
]

# (book_index, price, condition). Prices are spread deliberately, and one is 0:
# a free giveaway is a real thing on campus and the tile has to say "free"
# rather than treating it as a missing price.
LISTINGS = [
    (0, 450, 'like_new'),
    (0, 380, 'noted'),
    (1, 600, 'new'),
    (2, 320, 'noted'),
    (2, 290, 'damaged'),
    (2, 350, 'like_new'),
    (3,   0, 'noted'),
    (4, 700, 'new'),
    (4, 650, 'like_new'),
    (5, 520, 'noted'),
]

# (book_index, number of people waiting)
WAITLIST = [(6, 4), (1, 2)]

SELLER_COUNT = 5

# Pagination fixture for the home page's first book (Effective Java): enough
# listings to exercise the book/listing-detail pager, spread across many
# distinct sellers rather than one — the whole point of this script over
# seed_books_and_listings.py is avoiding the "667 sellers" look of one person
# owning everything.
BULK_LISTING_BOOK_INDEX = 0
BULK_LISTING_COUNT = 200
BULK_LISTING_SELLER_COUNT = 40


def get_user(tag, school, region):
    """A verified campus user; /sell gates on both login and verification."""
    user, created = User.objects.get_or_create(
        email=f'demo_{region.code.lower()}_{tag}@test.com',
        defaults={
            'first_name': f'Demo{region.code}{tag}',
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
            'edu_email': f'demo_{region.code.lower()}_{tag}@{school.email_domain}',
            'verified_at': timezone.now(),
            'is_active': True,
        }
    )
    return user


def run(region_code='TW'):
    region = Region.objects.get(code=region_code)
    school = School.objects.filter(region=region).first()
    if school is None:
        print(f"WARNING: no School rows exist for region {region.code} — users and listings will have school=None,")
        print("         and the school filter on the home page will not match them.")
    category = Category.objects.filter(region=region).first()

    books = []
    for isbn, title, authors, publisher in BOOKS:
        # Make ISBN unique per region to satisfy globally unique isbn13 constraint
        if region.code == 'HK':
            isbn = isbn.replace('978', '979')
        elif region.code != 'TW':
            isbn = f"{isbn[:-2]}{region.code}"
            
        book, _ = Book.objects.get_or_create(
            isbn13=isbn,
            region=region,
            defaults={
                'title': title,
                'authors': authors,
                'publisher': publisher,
                'published_date': '2020',
                # must be one of SOURCE_CHOICES: listed/preseed/manual/
                # google_api/openlibrary_api
                'source': 'preseed',
                # Left blank on purpose: exercises the title-initial cover
                # placeholder, and avoids shipping images that are not the
                # real cover. Fill these in from Google Books if you want art.
                'cover_url': '',
            },
        )
        books.append(book)
    print(f"{len(books)} books ready.")

    created = 0
    for i, (book_idx, price, condition) in enumerate(LISTINGS):
        seller = get_user(f'seller{i % SELLER_COUNT}', school, region)
        # created one at a time rather than bulk_create, so save() and any
        # signals still run. The volume here is trivial, so there is nothing
        # to gain from skipping them.
        _, was_created = Listing.objects.get_or_create(
            book=books[book_idx],
            seller=seller,
            price=to_minor(price if region.code == 'TW' else price / 4, region.currency.code),
            condition=condition,
            region=region,
            defaults={
                'currency': region.currency,
                'category': category,
                'school': school,
                'status': 'active',
                'description': f'Demo listing for {books[book_idx].title}.',
            },
        )
        created += int(was_created)
    print(f"{created} new listings created ({len(LISTINGS)} defined).")

    bulk_book = books[BULK_LISTING_BOOK_INDEX]
    bulk_sellers = [get_user(f'bulkseller{n}', school, region) for n in range(BULK_LISTING_SELLER_COUNT)]
    existing_bulk = Listing.objects.filter(book=bulk_book, description='Pagination fixture listing.').count()
    conditions = ['new', 'like_new', 'noted', 'damaged']
    statuses = ['active', 'reserved', 'sold']
    to_create = []
    for n in range(existing_bulk, BULK_LISTING_COUNT):
        to_create.append(Listing(
            book=bulk_book,
            seller=bulk_sellers[n % BULK_LISTING_SELLER_COUNT],
            region=region,
            currency=region.currency,
            price=seed_price(region),
            condition=random.choice(conditions),
            category=category,
            school=school,
            status=random.choice(statuses),
            description='Pagination fixture listing.',
        ))
    if to_create:
        Listing.objects.bulk_create(to_create)
    print(f"{len(to_create)} new pagination-fixture listings created for "
          f"'{bulk_book.title}' ({existing_bulk + len(to_create)}/{BULK_LISTING_COUNT} total).")

    subs = 0
    for book_idx, want_count in WAITLIST:
        for n in range(want_count):
            # unique_together is (user, book), so those are the lookup keys.
            # created_at is auto_now_add and cannot be set from here.
            _, was_created = Subscription.objects.get_or_create(
                user=get_user(f'wanter{n}', school, region),
                book=books[book_idx],
                region=region,
                defaults={'school': school},
            )
            subs += int(was_created)
    print(f"{subs} new subscriptions created for region {region.code}.")

    print("\nExpected on the home page:")
    print("  Recently added : 6 books, seller counts of 1-3 (not 667)")
    print("  Principles of Economics -> 免費 / Free, not 洽賣家")
    print("  no cover images         -> title-initial placeholder")
    print("  Most Wanted    : Introduction to Algorithms, 4 waiting")
    print("\nNote: 'Introduction to Algorithms' has no listings, so it is absent")
    print("from Recently added by design — that endpoint filters on")
    print("listings__status='active'.")


if __name__ == '__main__':
    region_code = sys.argv[1] if len(sys.argv) > 1 else 'TW'
    run(region_code)

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

Run:  .venv/bin/python seed_homepage_demo.py
"""
import os
import sys
import django
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cycleuni.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_homepage_demo.py with DEBUG=False "
        "(this script creates fake users and listings)."
    )
    sys.exit(1)

from catalog.models import Book
from listings.models import Listing
from core.models import Category
from accounts.models import User, School
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


def get_user(tag, school):
    """A verified campus user; /sell gates on both login and verification."""
    user, created = User.objects.get_or_create(
        email=f'demo_{tag}@test.com',
        defaults={
            'first_name': f'Demo{tag}',
            'last_name': 'User',
            'edu_email': f'demo_{tag}@ntu.edu.tw',
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
        print("WARNING: no School rows exist — users and listings will have school=None,")
        print("         and the school filter on the home page will not match them.")
    category = Category.objects.first()

    books = []
    for isbn, title, authors, publisher in BOOKS:
        book, _ = Book.objects.get_or_create(
            isbn13=isbn,
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
        seller = get_user(f'seller{i % SELLER_COUNT}', school)
        # created one at a time rather than bulk_create, so save() and any
        # signals still run. The volume here is trivial, so there is nothing
        # to gain from skipping them.
        _, was_created = Listing.objects.get_or_create(
            book=books[book_idx],
            seller=seller,
            price=price,
            condition=condition,
            defaults={
                'category': category,
                'school': school,
                'status': 'active',
                'description': f'Demo listing for {books[book_idx].title}.',
            },
        )
        created += int(was_created)
    print(f"{created} new listings created ({len(LISTINGS)} defined).")

    subs = 0
    for book_idx, want_count in WAITLIST:
        for n in range(want_count):
            # unique_together is (user, book), so those are the lookup keys.
            # created_at is auto_now_add and cannot be set from here.
            _, was_created = Subscription.objects.get_or_create(
                user=get_user(f'wanter{n}', school),
                book=books[book_idx],
                defaults={'school': school},
            )
            subs += int(was_created)
    print(f"{subs} new subscriptions created.")

    print("\nExpected on the home page:")
    print("  Recently added : 6 books, seller counts of 1-3 (not 667)")
    print("  Principles of Economics -> 免費 / Free, not 洽賣家")
    print("  no cover images         -> title-initial placeholder")
    print("  Most Wanted    : Introduction to Algorithms, 4 waiting")
    print("\nNote: 'Introduction to Algorithms' has no listings, so it is absent")
    print("from Recently added by design — that endpoint filters on")
    print("listings__status='active'.")


if __name__ == '__main__':
    run()

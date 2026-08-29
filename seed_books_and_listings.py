import os
import sys
import django
import random
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cycleuni.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_books_and_listings.py with DEBUG=False "
        "(this script creates fake users/listings and must never run against production)."
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

def seed_data(region_code='TW'):
    region = Region.objects.get(code=region_code)
    book_count = 1
    listing_count = 2000
    print(f"Seeding {book_count} books and {listing_count} listings per book for region {region.code}...")

    # Ensure a test user exists
    user, created = User.objects.get_or_create(
        email='test@test.com',
        defaults={
            'first_name': 'Test',
            'last_name': 'User',
        }
    )
    if created:
        user.set_password('testpassword')
        user.save()
        print(f"Created test user: {user.email}")
    else:
        print(f"Using existing test user: {user.email}")

    # Ensure the user has a school in this region
    school = School.objects.filter(region=region).first()
    if not school:
        print(f"No schools found for region {region.code}, please seed schools first.")
        return

    # Ensure user is verified in this region
    RegionVerification.objects.update_or_create(
        user=user,
        region=region,
        defaults={
            'school': school,
            'edu_email': f'test@{school.email_domain}',
            'verified_at': timezone.now(),
            'is_active': True,
        }
    )
    print(f"Verified test user in region {region.code} at school {school.name}.")

    # Get a category for this region
    category = Category.objects.filter(region=region).first()

    # Create unique books
    books = []
    print("Creating books...")
    for i in range(1, book_count + 1):
        if region.code == 'HK':
            isbn = f"976{i:010d}"
        else:
            isbn = f"978{i:010d}"
            
        book, created = Book.objects.get_or_create(
            isbn13=isbn,
            region=region,
            defaults={
                'title': f'Sample Book {i}',
                'authors': f'Author {i}',
                'publisher': f'Publisher {i}',
                'published_date': '2023-01-01',
                'source': 'preseed',
                'cover_url': f'https://picsum.photos/seed/{isbn}/200/300'
            }
        )
        books.append(book)

    print("Books ready. Creating listings...")

    # Create 200 listings per book
    conditions = ['new', 'like_new', 'noted', 'damaged']
    statuses = ['active', 'reserved', 'sold']
    
    count = 0
    listings_to_create = []
    
    for book in books:
        # Check how many listings currently exist for this book by this user
        existing = Listing.objects.filter(book=book, seller=user).count()
        needed = listing_count - existing
        
        for j in range(needed):
            listings_to_create.append(
                Listing(
                    book=book,
                    seller=user,
                    region=region,
                    currency=region.currency,
                    price=seed_price(region),
                    condition=random.choice(conditions),
                    category=category,
                    status=random.choice(statuses),
                    course_name=f'Course {j+1}' if random.choice([True, False]) else '',
                    description=f'This is a sample description for listing {j+1} of book {book.title}.',
                )
            )
            count += 1
            
            # Batch create every 5000 listings to save memory
            if len(listings_to_create) >= 5000:
                Listing.objects.bulk_create(listings_to_create)
                print(f"Inserted {count} listings so far...")
                listings_to_create = []
                
    if listings_to_create:
        Listing.objects.bulk_create(listings_to_create)

    print(f"Successfully seeded {count} new listings. Total {book_count * listing_count} listings should now exist for these {book_count} books.")

    print("Seeding subscriptions for Most Wanted...")
    from subscriptions.models import Subscription
    
    dummy_users = []
    print("Creating 100 dummy users for subscriptions...")
    for i in range(100):
        dummy_user, _ = User.objects.get_or_create(
            email=f'dummy{region.code.lower()}{i}@test.com',
            defaults={
                'first_name': f'Dummy{region.code}{i}',
                'last_name': 'User',
            }
        )
        RegionVerification.objects.update_or_create(
            user=dummy_user,
            region=region,
            defaults={
                'school': school,
                'edu_email': f'dummy{region.code.lower()}{i}@{school.email_domain}',
                'verified_at': timezone.now(),
                'is_active': True,
            }
        )
        dummy_users.append(dummy_user)
        
    wanted_books = random.sample(books, min(100, len(books)))
    subs_to_create = []
    print("Assigning random subscriptions...")
    for wb in wanted_books:
        want_count = random.randint(1, 100)
        selected_users = random.sample(dummy_users, want_count)
        
        # Avoid existing subscriptions
        existing_subs = set(Subscription.objects.filter(book=wb, region=region).values_list('user_id', flat=True))
        
        for u in selected_users:
            if u.id not in existing_subs:
                subs_to_create.append(
                    Subscription(
                        user=u,
                        book=wb,
                        school=school,
                        region=region,
                        created_at=timezone.now()
                    )
                )
                
    if subs_to_create:
        Subscription.objects.bulk_create(subs_to_create)
    print(f"Successfully seeded {len(subs_to_create)} subscriptions for {len(wanted_books)} books.")

if __name__ == '__main__':
    region_code = sys.argv[1] if len(sys.argv) > 1 else 'TW'
    seed_data(region_code)

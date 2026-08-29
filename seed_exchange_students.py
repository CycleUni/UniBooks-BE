"""Seed accounts that hold verifications in more than one region.

Every other seed script builds a separate pool of demo users per region
(`bulkbook_tw_*` vs `bulkbook_hk_*`), which is realistic — most students only
ever study in one place — but it leaves the multi-region case with no data at
all. That is misleading in a specific way: browsing the demo site, sellers
appear to be partitioned by region, as though accounts themselves carried a
region. They do not. `User` has no region column; only RegionVerification is
per region, and one account can hold several.

These "exchange students" exist so that case is visible and testable: the
admin user list shows a real multi-region `regions` array, the public seller
page has someone whose school differs depending on which market you browse,
and the per-region trading gate has a subject that passes in both.

Idempotent — re-running updates the same accounts rather than adding more.
"""

import os
import random
import sys

import django
from django.utils import timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from accounts.models import School, User, RegionVerification
from catalog.models import Book
from core.models import Region
from core.money import to_minor
from listings.models import Listing

EXCHANGE_STUDENT_COUNT = 5

# Same major-unit ranges the other seeds use, so these accounts' listings sit
# alongside the rest instead of standing out as an odd price band.
PRICE_RANGE = {'TW': (100, 1000), 'HK': (25, 250)}


def price_for(region):
    low, high = PRICE_RANGE.get(region.code, PRICE_RANGE['TW'])
    return to_minor(random.randint(low, high), region.currency.code)


def run():
    regions = list(Region.objects.filter(is_active=True).order_by('code'))
    if len(regions) < 2:
        print('Fewer than two active regions — nothing to demonstrate. Run seed_regions.py first.')
        return

    schools = {}
    for region in regions:
        school = School.objects.filter(region=region).order_by('id').first()
        if not school:
            print(f'No school in {region.code}; run seed_schools.py first.')
            return
        schools[region.code] = school

    created_users = 0
    created_listings = 0

    for i in range(EXCHANGE_STUDENT_COUNT):
        email = f'exchange{i}@test.com'
        user, was_created = User.objects.get_or_create(
            email=email,
            defaults={'first_name': f'Exchange{i}', 'last_name': 'Student'},
        )
        if was_created:
            user.set_unusable_password()
            user.save()
        created_users += int(was_created)

        for region in regions:
            school = schools[region.code]
            RegionVerification.objects.update_or_create(
                user=user,
                region=region,
                defaults={
                    'school': school,
                    # Each region's verification carries that region's campus
                    # address — the whole point is that one account proves
                    # student status separately in each market.
                    'edu_email': f'exchange{i}@{school.email_domain}',
                    'verified_at': timezone.now(),
                    'is_active': True,
                },
            )

            # One listing per region so they show up as a seller in both, which
            # is what makes the shared-account behaviour visible in the UI.
            book = Book.objects.filter(region=region).order_by('id').first()
            if not book:
                continue
            _, listing_created = Listing.objects.get_or_create(
                book=book,
                seller=user,
                region=region,
                defaults={
                    'school': school,
                    'currency': region.currency,
                    'price': price_for(region),
                    'condition': 'like_new',
                    'status': 'active',
                    'description': f'Listed by an exchange student verified in {region.code}.',
                },
            )
            created_listings += int(listing_created)

    print(f'{created_users} new exchange-student accounts, {created_listings} new listings.')
    for user in User.objects.filter(email__startswith='exchange').order_by('email'):
        codes = sorted(user.region_verifications.values_list('region_id', flat=True))
        print(f'  {user.email:<22} verified in {codes}')


if __name__ == '__main__':
    run()

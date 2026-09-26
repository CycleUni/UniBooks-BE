import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unibooks.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_categories.py with DEBUG=False "
        "(this script must never run against production)."
    )
    sys.exit(1)

from core.default_categories import DEFAULT_CATEGORIES, category_defaults
from core.models import Category, Region

# Resets every region's default categories to the shared definitions,
# overwriting local edits. New regions get them automatically; this is for
# putting a development database back to a known state.
for region in Region.objects.all():
    for order, item in enumerate(DEFAULT_CATEGORIES, start=1):
        Category.objects.update_or_create(
            slug=item['slug'],
            region=region,
            defaults=category_defaults(item, order),
        )

print("Categories seeded!")

from django.db import migrations

def forward_func(apps, schema_editor):
    Region = apps.get_model('core', 'Region')
    Currency = apps.get_model('core', 'Currency')
    School = apps.get_model('accounts', 'School')
    User = apps.get_model('accounts', 'User')
    RegionVerification = apps.get_model('accounts', 'RegionVerification')
    Book = apps.get_model('catalog', 'Book')
    Listing = apps.get_model('listings', 'Listing')
    Category = apps.get_model('core', 'Category')
    Order = apps.get_model('orders', 'Order')
    Advertiser = apps.get_model('ads', 'Advertiser')
    Subscription = apps.get_model('subscriptions', 'Subscription')

    Language = apps.get_model('core', 'Language')

    twd, _ = Currency.objects.get_or_create(
        code='TWD', defaults={'symbol': 'NT$', 'decimal_places': 0}
    )
    zh_tw, _ = Language.objects.get_or_create(
        code='zh-TW', defaults={'name': 'Traditional Chinese (Taiwan)', 'native_name': '繁體中文（台灣）'}
    )
    tw, _ = Region.objects.get_or_create(
        code='TW', defaults={
            'name': 'Taiwan',
            'currency': twd,
            'default_language': zh_tw,
            'is_active': True
        }
    )

    School.objects.update(region=tw)
    Book.objects.update(region=tw)
    Listing.objects.update(region=tw, currency=twd)
    Category.objects.update(region=tw)
    Order.objects.update(region=tw, currency=twd)
    Subscription.objects.update(region=tw)

    # User -> RegionVerification
    for user in User.objects.exclude(verified_at__isnull=True):
        # Data quality fix: Do not fall back to user.email if edu_email is null.
        # Instead, leave edu_email null and flag it as a manual verification.
        edu = user.edu_email
        is_manual = True if not edu else False
        RegionVerification.objects.create(
            user=user,
            region=tw,
            school=user.school,
            edu_email=edu,
            is_manual_verification=is_manual,
            verified_at=user.verified_at,
            last_reverified_at=user.last_reverified_at,
            is_active=True
        )

    # Advertiser
    for ad in Advertiser.objects.all():
        ad.all_regions = False
        ad.save()
        ad.regions.add(tw)

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0005_category_region'),
        ('accounts', '0009_school_region_user_managed_regions_and_more'),
        ('ads', '0008_advertiser_all_regions_advertiser_regions'),
        ('catalog', '0006_book_region'),
        ('listings', '0008_listing_currency_listing_region'),
        ('orders', '0007_order_currency_order_region'),
        ('subscriptions', '0002_subscription_region'),
    ]

    operations = [
        migrations.RunPython(forward_func, migrations.RunPython.noop),
    ]

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

@pytest.mark.django_db(transaction=True)
def test_backfill_regions_migration():
    executor = MigrationExecutor(connection)
    
    app = 'core'
    migrate_from = [(app, '0005_category_region')]
    migrate_to = [(app, '0006_backfill_regions')]
    
    # We must roll back *everything* that depends on 0006
    # For example: listings 0009, orders 0008, accounts 0010, catalog 0007, subscriptions 0003
    # Rolling back all these might be slow, but it's correct.
    executor.loader.build_graph()
    
    # Easiest way to roll back: rollback all the final migrations of the apps to the state before they altered null=False
    rollback_targets = [
        ('accounts', '0009_school_region_user_managed_regions_and_more'),
        ('listings', '0008_listing_currency_listing_region'),
        ('orders', '0007_order_currency_order_region'),
        ('catalog', '0006_book_region'),
        ('subscriptions', '0002_subscription_region'),
        ('core', '0005_category_region'),
        ('ads', '0008_advertiser_all_regions_advertiser_regions'),
    ]
    
    executor.migrate(rollback_targets)
    
    old_apps = executor.loader.project_state(rollback_targets).apps
    
    User = old_apps.get_model('accounts', 'User')
    Listing = old_apps.get_model('listings', 'Listing')
    Book = old_apps.get_model('catalog', 'Book')
    
    user = User.objects.create(email="testmig@example.com", first_name="A", last_name="B")
    # Verified user
    user.verified_at = "2026-01-01 00:00:00"
    user.edu_email = "testmig@test.edu.tw"
    user.save()
    
    book = Book.objects.create(title="Old Book", isbn13="1234567890123", source="manual")
    listing = Listing.objects.create(book=book, seller=user, price=100, condition="new", status="active")
    
    # Now run forward
    executor.loader.build_graph()
    # Apply 0006
    executor.migrate([('core', '0006_backfill_regions')])
    
    new_apps = executor.loader.project_state([('core', '0006_backfill_regions')]).apps
    
    NewListing = new_apps.get_model('listings', 'Listing')
    RegionVerification = new_apps.get_model('accounts', 'RegionVerification')
    
    migrated_listing = NewListing.objects.get(id=listing.id)
    assert migrated_listing.region.code == 'TW'
    assert migrated_listing.currency.code == 'TWD'
    assert migrated_listing.price == 100
    
    verifications = RegionVerification.objects.filter(user_id=user.id)
    assert verifications.count() == 1
    assert verifications[0].region.code == 'TW'
    assert verifications[0].edu_email == "testmig@test.edu.tw"


from django.db import migrations


def forwards(apps, schema_editor):
    """Give every region that has no categories the default colleges.

    0002_seed_categories only ever covered Taiwan (0006 attached its rows to
    TW), so Hong Kong — created later by seed_regions.py or the admin — had
    an empty category rail unless someone seeded it by hand. Regions that
    already have categories keep them untouched.
    """
    from core.default_categories import seed_default_categories

    Region = apps.get_model('core', 'Region')
    Category = apps.get_model('core', 'Category')
    for region in Region.objects.all():
        seed_default_categories(region, category_model=Category)
    _invalidate_home_cache()


def _invalidate_home_cache():
    # The home page caches categories for 24h and only admin edits clear it;
    # this migration's historical models fire no signals. Best effort: a
    # cache outage must not fail the migration.
    try:
        from accounts.views.home import invalidate_home_static_cache
        invalidate_home_static_cache()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_consolidated'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

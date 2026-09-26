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


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_consolidated'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

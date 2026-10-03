from django.db import migrations


def forwards(apps, schema_editor):
    """Give every existing region its default cities (core.default_cities).

    A region created later gets them from seed_regions.py. Regions that
    already have cities keep them untouched.
    """
    from core.default_cities import seed_default_cities

    Region = apps.get_model('core', 'Region')
    City = apps.get_model('core', 'City')
    for region in Region.objects.all():
        seed_default_cities(region, city_model=City)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_city'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

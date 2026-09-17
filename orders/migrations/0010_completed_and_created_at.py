from django.db import migrations, models
from django.db.models import F


def backfill_completed_at(apps, schema_editor):
    """Completed orders get updated_at as their completion time.

    It is the same approximation the statistics used before this column
    existed: exact unless the order was saved again after completion.
    """
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(status='completed', completed_at__isnull=True).update(completed_at=F('updated_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0009_order_region_created_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_completed_at, migrations.RunPython.noop),
    ]

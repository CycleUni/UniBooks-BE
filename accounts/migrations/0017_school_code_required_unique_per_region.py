import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3 for School.code: required, and unique per region (not
    globally — Taiwan and Hong Kong each have an HKU)."""

    dependencies = [
        ('accounts', '0016_backfill_school_code'),
    ]

    operations = [
        migrations.AlterField(
            model_name='school',
            name='code',
            field=models.CharField(blank=True, help_text='Short code, unique within the region, e.g. NTU. Left blank, one is derived from the email domain.', max_length=20, validators=[django.core.validators.RegexValidator('^[A-Za-z0-9-]+$', 'Use only letters, digits and "-".')]),
        ),
        migrations.AddConstraint(
            model_name='school',
            constraint=models.UniqueConstraint(fields=('region', 'code'), name='school_unique_code_per_region'),
        ),
    ]

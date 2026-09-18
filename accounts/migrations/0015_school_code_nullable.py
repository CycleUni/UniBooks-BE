import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3 for School.code: the column, nullable, so the existing
    rows can exist before 0016 fills them in. 0017 makes it required."""

    dependencies = [
        ('accounts', '0014_user_email_and_site_language'),
    ]

    operations = [
        migrations.AddField(
            model_name='school',
            name='code',
            field=models.CharField(blank=True, help_text='Short code, unique within the region, e.g. NTU. Left blank, one is derived from the email domain.', max_length=20, null=True, validators=[django.core.validators.RegexValidator('^[A-Za-z0-9-]+$', 'Use only letters, digits and "-".')]),
        ),
    ]

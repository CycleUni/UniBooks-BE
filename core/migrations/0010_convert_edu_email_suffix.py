import json
from django.db import migrations, models

def convert_to_json(apps, schema_editor):
    Region = apps.get_model('core', 'Region')
    for region in Region.objects.all():
        # It's currently a string before AlterField
        val = region.edu_email_suffix
        if val and not val.startswith('['):
            region.edu_email_suffix = json.dumps([val])
            region.save(update_fields=['edu_email_suffix'])
        elif not val:
            region.edu_email_suffix = json.dumps([])
            region.save(update_fields=['edu_email_suffix'])

def reverse_convert(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_complete_tw_region_config'),
    ]

    operations = [
        migrations.RunPython(convert_to_json, reverse_convert),
        migrations.AlterField(
            model_name='region',
            name='edu_email_suffix',
            field=models.JSONField(blank=True, default=list),
        ),
    ]

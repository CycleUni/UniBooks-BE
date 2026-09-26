# Consolidated on 2026-09-27: replaces this app's 8 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import ads.validators
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('ads', '0001_initial'),
        ('ads', '0002_advertiser_all_schools_advertiser_schools'),
        ('ads', '0003_alter_ad_advertiser_alter_ad_clicks_count_and_more'),
        ('ads', '0004_ad_headline_ad_slot_index_ad_subheadline_and_more'),
        ('ads', '0005_alter_ad_image_url'),
        ('ads', '0006_ad_labels'),
        ('ads', '0007_ad_show_in_hero'),
        ('ads', '0008_advertiser_all_regions_advertiser_regions'),
    ]

    dependencies = [
        ('accounts', '0001_consolidated'),
        ('core', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Advertiser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('all_regions', models.BooleanField(default=False)),
                ('company_name', models.CharField(help_text='Company name', max_length=255)),
                ('contact_email', models.EmailField(help_text='Contact email', max_length=254)),
                ('contact_phone', models.CharField(blank=True, help_text='Contact phone', max_length=50)),
                ('all_schools', models.BooleanField(default=True, help_text='Whether it applies to all schools')),
                ('is_active', models.BooleanField(default=True, help_text='Whether it is active')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('regions', models.ManyToManyField(blank=True, related_name='advertisers', to='core.region')),
                ('schools', models.ManyToManyField(blank=True, help_text='Specific schools it applies to', related_name='advertisers', to='accounts.school')),
                ('user', models.OneToOneField(blank=True, help_text='Reserved for future advertiser login', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='advertiser_profile', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Ad',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(help_text='Ad title, for internal identification', max_length=255)),
                ('image_url', models.URLField(help_text='Recommended 5:7 portrait, min 870x1218 (for largest cell 434x607 @2x)', max_length=500)),
                ('target_url', models.URLField(help_text='Target URL after click', max_length=500)),
                ('position', models.CharField(choices=[('home_banner', 'Home Banner')], default='home_banner', help_text='Ad position', max_length=50)),
                ('headline', models.CharField(blank=True, help_text='Headline', max_length=255)),
                ('subheadline', models.CharField(blank=True, help_text='Subheadline', max_length=255)),
                ('slot_index', models.PositiveIntegerField(default=1, help_text='Display order (1-indexed, max 200)', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(200)])),
                ('labels', models.JSONField(blank=True, default=list, help_text='Custom labels (max 3, max 15 chars each)', validators=[ads.validators.validate_ad_labels])),
                ('start_date', models.DateTimeField(help_text='Start date')),
                ('end_date', models.DateTimeField(help_text='End date')),
                ('is_active', models.BooleanField(default=True, help_text='Whether it is active')),
                ('show_in_hero', models.BooleanField(default=False, help_text='Whether to display this ad as the first card in the home page hero cover stack')),
                ('clicks_count', models.IntegerField(default=0, help_text='Click count statistics (update via F() expression only)')),
                ('views_count', models.IntegerField(default=0, help_text='View count statistics (update via F() expression only)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('advertiser', models.ForeignKey(help_text='Advertiser it belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='ads', to='ads.advertiser')),
            ],
            options={
                'indexes': [models.Index(fields=['is_active', 'position', 'start_date', 'end_date'], name='ad_active_pos_period_idx')],
            },
        ),
    ]

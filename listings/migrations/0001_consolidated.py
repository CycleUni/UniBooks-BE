# Consolidated on 2026-09-27: replaces this app's 11 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.contrib.postgres.indexes
import django.db.models.deletion
import uuid
from django.conf import settings
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('listings', '0001_initial'),
        ('listings', '0002_remove_listing_delivery_methods_and_more'),
        ('listings', '0003_alter_listing_condition_alter_listing_status'),
        ('listings', '0004_alter_listing_photos'),
        ('listings', '0005_listing_listing_course_trgm_idx'),
        ('listings', '0006_alter_listing_photos'),
        ('listings', '0007_listing_professor_name_and_more'),
        ('listings', '0008_listing_currency_listing_region'),
        ('listings', '0009_alter_listing_currency_alter_listing_region'),
        ('listings', '0010_backfill_listing_school'),
        ('listings', '0011_listing_feed_indexes'),
    ]

    dependencies = [
        ('accounts', '0001_consolidated'),
        ('catalog', '0001_consolidated'),
        ('core', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # The gin_trgm_ops indexes below need pg_trgm; a no-op off Postgres.
        TrigramExtension(),
        migrations.CreateModel(
            name='Listing',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('price', models.PositiveIntegerField()),
                ('condition', models.CharField(choices=[('new', 'new'), ('like_new', 'like_new'), ('noted', 'noted'), ('damaged', 'damaged')], max_length=20)),
                ('private_note', models.TextField(blank=True)),
                ('description', models.TextField(blank=True)),
                ('photos', models.JSONField(default=list, help_text='Array of full photo URLs (public URLs after R2 storage, see ListingUploadURLView)')),
                ('status', models.CharField(choices=[('active', 'active'), ('reserved', 'reserved'), ('sold', 'sold'), ('removed', 'removed')], default='active', max_length=20)),
                ('course_name', models.CharField(blank=True, default='', max_length=255)),
                ('professor_name', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('book', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listings', to='catalog.book')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listings', to='core.category')),
                ('currency', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.currency')),
                ('region', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.region')),
                ('school', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='listings', to='accounts.school')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['region', 'status', '-created_at'], name='listing_region_status_idx'), models.Index(fields=['region', 'status', 'category'], name='listing_region_status_cat_idx'), models.Index(fields=['region', 'status', 'course_name'], name='listing_region_status_crs_idx'), django.contrib.postgres.indexes.GinIndex(fields=['course_name'], name='listing_course_trgm_idx', opclasses=['gin_trgm_ops']), django.contrib.postgres.indexes.GinIndex(fields=['professor_name'], name='listing_professor_trgm_idx', opclasses=['gin_trgm_ops'])],
            },
        ),
    ]

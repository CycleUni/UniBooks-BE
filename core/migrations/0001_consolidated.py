# Consolidated on 2026-09-27: replaces this app's 11 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('core', '0001_initial'),
        ('core', '0002_seed_categories'),
        ('core', '0003_currency_language_region'),
        ('core', '0004_alter_currency_options_alter_language_options_and_more'),
        ('core', '0005_category_region'),
        ('core', '0006_backfill_regions'),
        ('core', '0007_alter_category_region_and_more'),
        ('core', '0008_alter_category_slug'),
        ('core', '0009_complete_tw_region_config'),
        ('core', '0010_convert_edu_email_suffix'),
        ('core', '0011_throttle_counter'),
    ]

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Currency',
            fields=[
                ('code', models.CharField(max_length=3, primary_key=True, serialize=False)),
                ('symbol', models.CharField(max_length=8)),
                ('decimal_places', models.PositiveSmallIntegerField(default=2)),
                ('symbol_position', models.CharField(choices=[('prefix', 'prefix'), ('suffix', 'suffix')], default='prefix', max_length=6)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='Language',
            fields=[
                ('code', models.CharField(max_length=10, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('native_name', models.CharField(max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.CreateModel(
            name='AuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=100)),
                ('meta', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Region',
            fields=[
                ('code', models.CharField(max_length=2, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('translations', models.JSONField(blank=True, default=dict)),
                ('timezone', models.CharField(default='Asia/Taipei', max_length=64)),
                ('search_engines', models.JSONField(default=list)),
                ('edu_email_suffix', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('currency', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='regions', to='core.currency')),
                ('default_language', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='default_for_regions', to='core.language')),
                ('languages', models.ManyToManyField(related_name='regions', to='core.language')),
            ],
            options={
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.CreateModel(
            name='ThrottleCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=255)),
                ('window_start', models.BigIntegerField(db_index=True)),
                ('count', models.PositiveIntegerField(default=0)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('key', 'window_start'), name='one_throttle_counter_per_window')],
            },
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField()),
                ('title', models.CharField(max_length=100)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('translations', models.JSONField(blank=True, default=dict)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('region', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='categories', to='core.region')),
            ],
            options={
                'verbose_name_plural': 'categories',
                'ordering': ['sort_order', 'id'],
                'constraints': [models.UniqueConstraint(fields=('slug', 'region'), name='category_slug_region_uniq')],
            },
        ),
    ]

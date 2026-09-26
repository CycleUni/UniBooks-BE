# Consolidated on 2026-09-27: replaces this app's 20 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('accounts', '0001_initial'),
        ('accounts', '0002_user_avatar_url'),
        ('accounts', '0003_college'),
        ('accounts', '0004_remove_college_school'),
        ('accounts', '0005_delete_college'),
        ('accounts', '0006_user_last_seen_bought_orders_at_and_more'),
        ('accounts', '0007_school_school_trgm_idx_user_user_trgm_idx'),
        ('accounts', '0008_alter_school_email_domain_alter_user_avatar_url_and_more'),
        ('accounts', '0009_school_region_user_managed_regions_and_more'),
        ('accounts', '0010_remove_user_user_trgm_idx_and_more'),
        ('accounts', '0011_alter_regionverification_edu_email'),
        ('accounts', '0012_user_deleted_at'),
        ('accounts', '0013_user_notify_new_message_email'),
        ('accounts', '0014_user_email_and_site_language'),
        ('accounts', '0015_school_code_nullable'),
        ('accounts', '0016_backfill_school_code'),
        ('accounts', '0017_school_code_required_unique_per_region'),
        ('accounts', '0018_school_request'),
        ('accounts', '0019_refresh_token_record'),
        ('accounts', '0020_one_time_token'),
    ]

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='OneTimeToken',
            fields=[
                ('token', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('purpose', models.CharField(choices=[('register', 'Registration'), ('edu_verify', 'Campus email verification'), ('password_reset', 'Password reset'), ('email_change', 'Email change')], max_length=20)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='RefreshTokenRecord',
            fields=[
                ('jti', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('rotated_at', models.DateTimeField(blank=True, null=True)),
                ('rotated_tokens', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='RegionVerification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('edu_email', models.EmailField(blank=True, help_text='Null for manual verifications.', max_length=254, null=True, unique=True)),
                ('is_manual_verification', models.BooleanField(default=False)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('last_reverified_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='School',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email_domain', models.CharField(help_text='e.g.: ntu.edu.tw', max_length=255, unique=True)),
                ('name', models.CharField(help_text='Canonical English name, e.g. National Taiwan University', max_length=255)),
                ('translations', models.JSONField(blank=True, default=dict, help_text='Localized fields per language, e.g. {"zh-TW": {"name": "國立台灣大學"}}')),
                ('code', models.CharField(blank=True, help_text='Short code, unique within the region, e.g. NTU. Left blank, one is derived from the email domain.', max_length=20, validators=[django.core.validators.RegexValidator('^[A-Za-z0-9-]+$', 'Use only letters, digits and "-".')])),
            ],
        ),
        migrations.CreateModel(
            name='SchoolRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('school_name', models.CharField(max_length=255)),
                ('school_website', models.URLField(max_length=500)),
                ('edu_email', models.EmailField(blank=True, default='', max_length=254)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('added', 'Added'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('admin_note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ('-created_at',),
            },
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('email', models.EmailField(help_text='Registration email, can be any email', max_length=254, unique=True)),
                ('first_name', models.CharField(default='', max_length=150)),
                ('last_name', models.CharField(default='', max_length=150)),
                ('avatar_url', models.URLField(blank=True, help_text='Avatar URL', max_length=500, null=True)),
                ('last_seen_bought_orders_at', models.DateTimeField(blank=True, help_text='Timestamp of the most recent bought order seen by the user', null=True)),
                ('last_seen_sold_orders_at', models.DateTimeField(blank=True, help_text='Timestamp of the most recent sold order seen by the user', null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('is_staff', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('notify_new_message_email', models.BooleanField(default=True, help_text='Email the user about a chat message that arrives while they are not on the site')),
                ('email_language', models.CharField(default='auto', help_text="Language for notification emails: 'auto' (follow site_language), or zh-TW / zh-HK / en", max_length=10)),
                ('site_language', models.CharField(blank=True, default='', help_text='The language the user last used the site in, as reported by the frontend', max_length=10)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
            ],
        ),
    ]

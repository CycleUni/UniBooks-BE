# Consolidated on 2026-09-27: replaces this app's 20 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.contrib.postgres.indexes
import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.contrib.postgres.operations import TrigramExtension
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
        ('accounts', '0001_consolidated'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0001_consolidated'),
    ]

    operations = [
        # The gin_trgm_ops indexes below need pg_trgm; a no-op off Postgres.
        TrigramExtension(),
        migrations.AddField(
            model_name='user',
            name='managed_regions',
            field=models.ManyToManyField(blank=True, related_name='managers', to='core.region'),
        ),
        migrations.AddField(
            model_name='user',
            name='user_permissions',
            field=models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions'),
        ),
        migrations.AddField(
            model_name='onetimetoken',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='one_time_tokens', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='refreshtokenrecord',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='refresh_tokens', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='regionverification',
            name='region',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='verifications', to='core.region'),
        ),
        migrations.AddField(
            model_name='regionverification',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='region_verifications', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='school',
            name='region',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schools', to='core.region'),
        ),
        migrations.AddField(
            model_name='regionverification',
            name='school',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='verifications', to='accounts.school'),
        ),
        migrations.AddField(
            model_name='schoolrequest',
            name='region',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='school_requests', to='core.region'),
        ),
        migrations.AddField(
            model_name='schoolrequest',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='school_requests', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='user',
            index=django.contrib.postgres.indexes.GinIndex(fields=['email', 'first_name', 'last_name'], name='user_trgm_idx', opclasses=['gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops']),
        ),
        migrations.AddIndex(
            model_name='onetimetoken',
            index=models.Index(fields=['user', 'purpose'], name='one_time_token_user_purpose'),
        ),
        migrations.AddIndex(
            model_name='refreshtokenrecord',
            index=models.Index(condition=models.Q(('rotated_at__isnull', True)), fields=['user'], name='refresh_token_live_by_user'),
        ),
        migrations.AddIndex(
            model_name='school',
            index=django.contrib.postgres.indexes.GinIndex(fields=['name', 'email_domain'], name='school_trgm_idx', opclasses=['gin_trgm_ops', 'gin_trgm_ops']),
        ),
        migrations.AddConstraint(
            model_name='school',
            constraint=models.UniqueConstraint(fields=('region', 'code'), name='school_unique_code_per_region'),
        ),
        migrations.AddConstraint(
            model_name='regionverification',
            constraint=models.UniqueConstraint(fields=('user', 'region'), name='one_verification_per_user_region'),
        ),
        migrations.AddIndex(
            model_name='schoolrequest',
            index=models.Index(fields=['region', 'status', '-created_at'], name='schoolreq_region_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='schoolrequest',
            constraint=models.UniqueConstraint(models.F('user'), models.F('region'), django.db.models.functions.text.Lower('school_name'), condition=models.Q(('status', 'pending')), name='one_pending_school_request_per_user_region_name'),
        ),
    ]

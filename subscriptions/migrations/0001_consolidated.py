# Consolidated on 2026-09-27: replaces this app's 3 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('subscriptions', '0001_initial'),
        ('subscriptions', '0002_subscription_region'),
        ('subscriptions', '0003_alter_subscription_region_and_more'),
    ]

    dependencies = [
        ('accounts', '0001_consolidated'),
        ('catalog', '0001_consolidated'),
        ('core', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('notified_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('book', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscriptions', to='catalog.book')),
                ('region', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.region')),
                ('school', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.school')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscriptions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['user', 'region'], name='subscriptio_user_id_f45386_idx')],
                'unique_together': {('user', 'book')},
            },
        ),
    ]

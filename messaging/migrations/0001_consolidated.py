# Consolidated on 2026-09-27: replaces this app's 7 historical migrations,
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
        ('messaging', '0001_initial'),
        ('messaging', '0002_conversation_latest_message_body_and_more'),
        ('messaging', '0003_conversation_buyer_last_read_at_and_more'),
        ('messaging', '0004_message'),
        ('messaging', '0005_conversation_buyer_deleted_at_and_more'),
        ('messaging', '0006_delete_message'),
        ('messaging', '0007_completed_and_created_at'),
    ]

    dependencies = [
        ('listings', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Conversation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('latest_message_body', models.TextField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True)),
                ('buyer_last_read_at', models.DateTimeField(blank=True, null=True)),
                ('seller_last_read_at', models.DateTimeField(blank=True, null=True)),
                ('buyer_deleted_at', models.DateTimeField(blank=True, null=True)),
                ('seller_deleted_at', models.DateTimeField(blank=True, null=True)),
                ('buyer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conversations_as_buyer', to=settings.AUTH_USER_MODEL)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conversations', to='listings.listing')),
            ],
            options={
                'unique_together': {('listing', 'buyer')},
            },
        ),
    ]

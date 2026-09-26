# Consolidated on 2026-09-27: replaces this app's 4 historical migrations,
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
        ('moderation', '0001_initial'),
        ('moderation', '0002_alter_report_reason_alter_report_status'),
        ('moderation', '0003_chatreport'),
        ('moderation', '0004_chatreport_flagged_message_ids'),
    ]

    dependencies = [
        ('listings', '0001_consolidated'),
        ('messaging', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.CharField(choices=[('harassment', 'harassment'), ('scam', 'scam'), ('spam', 'spam'), ('other', 'other')], max_length=20)),
                ('detail', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('open', 'open'), ('actioned', 'actioned'), ('dismissed', 'dismissed')], default='open', max_length=20)),
                ('flagged_message_ids', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_reports', to='messaging.conversation')),
                ('reported_party', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_reports_against', to=settings.AUTH_USER_MODEL)),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_reports', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.CharField(choices=[('fake', 'fake'), ('scam', 'scam'), ('other', 'other')], max_length=20)),
                ('detail', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('open', 'open'), ('actioned', 'actioned'), ('dismissed', 'dismissed')], default='open', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to='listings.listing')),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]

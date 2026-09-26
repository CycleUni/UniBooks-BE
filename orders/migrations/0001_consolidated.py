# Consolidated on 2026-09-27: replaces this app's 12 historical migrations,
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
        ('orders', '0001_initial'),
        ('orders', '0002_alter_order_status'),
        ('orders', '0003_order_stripe_checkout_url'),
        ('orders', '0004_order_cancel_reason'),
        ('orders', '0005_remove_order_delivery_method_and_more'),
        ('orders', '0006_order_meetup_location_order_meetup_time_and_more'),
        ('orders', '0007_order_currency_order_region'),
        ('orders', '0008_alter_order_currency_alter_order_region'),
        ('orders', '0009_order_region_created_idx'),
        ('orders', '0010_completed_and_created_at'),
        ('orders', '0011_order_meetup_reminder_sent_at_and_more'),
        ('orders', '0012_order_buyer_reminder_sent_at_and_more'),
    ]

    dependencies = [
        ('core', '0001_consolidated'),
        ('listings', '0001_consolidated'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('pending', 'Pending (Requested)'), ('accepted', 'Accepted (Awaiting Meetup)'), ('handed_over', 'Handed Over (Awaiting Buyer Confirmation)'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='pending', max_length=20)),
                ('cancel_reason', models.CharField(blank=True, max_length=50, null=True)),
                ('total_amount', models.PositiveIntegerField(help_text='Order total amount in TWD')),
                ('meetup_time', models.DateTimeField(blank=True, help_text='Agreed meetup time', null=True)),
                ('meetup_location', models.CharField(blank=True, help_text='Agreed meetup location', max_length=255)),
                ('meetup_reminder_sent_at', models.DateTimeField(blank=True, help_text='Timestamp when the meetup reminder was fully sent', null=True)),
                ('buyer_reminder_sent_at', models.DateTimeField(blank=True, help_text='Timestamp when meetup reminder was sent to buyer', null=True)),
                ('seller_reminder_sent_at', models.DateTimeField(blank=True, help_text='Timestamp when meetup reminder was sent to seller', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('buyer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='orders_bought', to=settings.AUTH_USER_MODEL)),
                ('currency', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.currency')),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='orders', to='listings.listing')),
                ('region', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.region')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='orders_sold', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Review',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rating', models.PositiveSmallIntegerField(blank=True, help_text='1 to 5 rating. Null if no-show report.', null=True)),
                ('comment', models.TextField(blank=True)),
                ('is_no_show', models.BooleanField(default=False, help_text='True if this is a report for a no-show.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='orders.order')),
                ('reviewee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews_received', to=settings.AUTH_USER_MODEL)),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews_given', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(fields=['region', 'created_at'], name='order_region_created_idx'),
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(fields=['status', 'meetup_time'], name='order_status_meetup_time_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='review',
            unique_together={('order', 'reviewer', 'reviewee')},
        ),
    ]

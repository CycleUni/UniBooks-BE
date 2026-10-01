import uuid
from django.db import models
from django.conf import settings
from listings.models import Listing
from listings.snapshot import fill_listing_snapshot

class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    REASON_CHOICES = [
        ('fake', 'fake'),
        ('scam', 'scam'),
        ('other', 'other'),
    ]

    STATUS_CHOICES = [
        ('open', 'open'),
        ('actioned', 'actioned'),
        ('dismissed', 'dismissed'),
    ]

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reports')
    # SET_NULL: a report must not vanish because the reported seller deleted
    # the listing. Seller and region are kept for staff scoping and review.
    listing = models.ForeignKey(Listing, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='listing_reports_against')
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    listing_ref = models.UUIDField(null=True, blank=True, db_index=True, editable=False)
    book_title = models.CharField(max_length=255, blank=True, default='')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    detail = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        fill_listing_snapshot(self, seller=True, region=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Report {self.id} for Listing {self.listing_ref}"


class ChatReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    REASON_CHOICES = [
        ('harassment', 'harassment'),
        ('scam', 'scam'),
        ('spam', 'spam'),
        ('other', 'other'),
    ]

    STATUS_CHOICES = [
        ('open', 'open'),
        ('actioned', 'actioned'),
        ('dismissed', 'dismissed'),
    ]

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_reports')
    conversation = models.ForeignKey('messaging.Conversation', on_delete=models.CASCADE, related_name='chat_reports')
    reported_party = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_reports_against')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    detail = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    flagged_message_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ChatReport {self.id} for Conv {self.conversation_id}"

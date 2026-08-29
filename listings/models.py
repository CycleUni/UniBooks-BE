import logging
import uuid
from urllib.parse import urlparse

from django.db import models
from django.conf import settings
from django.core.files.storage import default_storage
from django.contrib.postgres.indexes import GinIndex

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from catalog.models import Book
from core.cache import bump_cache_version

logger = logging.getLogger(__name__)

# NOTE: these two callables are no longer used by the current Listing model
# (the delivery_methods/payment_methods fields they defaulted were removed in
# migration 0002_remove_listing_delivery_methods_and_more.py), but migration
# 0001_initial.py still references them by import path
# ("listings.models.default_delivery"/"default_payment") to reconstruct its
# historical field defaults. Removing them breaks migration replay on a fresh
# database. Do not delete without first squashing/rewriting migration 0001.
def default_delivery():
    return ['meetup']

def default_payment():
    return ['cash']

class Listing(models.Model):
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    CONDITION_CHOICES = [
        ('new', 'new'),
        ('like_new', 'like_new'),
        ('noted', 'noted'),
        ('damaged', 'damaged'),
    ]

    STATUS_CHOICES = [
        ('active', 'active'),
        ('reserved', 'reserved'),
        ('sold', 'sold'),
        ('removed', 'removed'),
    ]

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='listings')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='listings')
    school = models.ForeignKey('accounts.School', on_delete=models.CASCADE, related_name='listings', null=True, blank=True)
    price = models.PositiveIntegerField()
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES)
    private_note = models.TextField(blank=True)
    description = models.TextField(blank=True)
    photos = models.JSONField(default=list, help_text="Array of full photo URLs (public URLs after R2 storage, see ListingUploadURLView)")
    category = models.ForeignKey('core.Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='listings')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    course_name = models.CharField(max_length=255, blank=True, default='')
    professor_name = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            GinIndex(
                name='listing_course_trgm_idx',
                fields=['course_name'],
                opclasses=['gin_trgm_ops']
            ),
            GinIndex(
                name='listing_professor_trgm_idx',
                fields=['professor_name'],
                opclasses=['gin_trgm_ops']
            )
        ]

    def __str__(self):
        return f"Listing {self.id} for {self.book.title} by {self.seller.email}"

    def delete(self, *args, **kwargs):
        """Delete the listing record and its associated R2 photos.

        When a listing is deleted, its uploaded photos should be removed from
        object storage (R2 or local FileSystemStorage) so orphaned files don't
        accumulate. Photo deletion failures are logged but do not prevent the
        database record from being removed — a stale object in the bucket is
        less harmful than an undeletable listing row.
        """
        if self.photos:
            for photo_url in self.photos:
                try:
                    # Extract the R2 key (path portion) from the full URL.
                    # Photo URLs have the form:
                    #   https://<custom_domain>/listings/<uuid>.<ext>
                    # The R2 key is: listings/<uuid>.<ext>
                    parsed = urlparse(photo_url)
                    key = parsed.path.lstrip('/')
                    default_storage.delete(key)
                    logger.info("Deleted photo %s for listing %s", key, self.id)
                except Exception:
                    logger.warning(
                        "Failed to delete photo %s for listing %s (listing will be deleted anyway)",
                        photo_url, self.id,
                        exc_info=True,
                    )

        return super().delete(*args, **kwargs)

@receiver([post_save, post_delete], sender=Listing)
def invalidate_listing_caches(sender, instance, **kwargs):
    """Drop every cached view that could now be stale, in every environment.

    Bumping generations rather than deleting keys is what makes this possible
    at all: `listing_list` and `book_detail` keys embed page/school/language/
    limit, so there is no finite set of keys to delete (see core.cache).

    This covers the order flow for free — accepting or completing an order
    calls `listing.save(update_fields=['status'])`, so post_save fires and the
    listing stops being advertised as available without any cache bookkeeping
    at the order call site.
    """
    bump_cache_version('listing_list')
    bump_cache_version(f'listing:{instance.pk}')
    # A book's detail page embeds its listings, so a sold or deleted listing
    # must invalidate it too — otherwise buyers keep seeing, and messaging
    # sellers about, an item that is already gone. Deliberately one shared
    # generation rather than per-book; see catalog.views.BookDetailView.
    bump_cache_version('book_detail')

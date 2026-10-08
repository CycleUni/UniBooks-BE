import logging
import uuid
from urllib.parse import urlparse

from django.db import models, transaction
from django.conf import settings
from django.core.files.storage import default_storage
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db.models.functions import Upper

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
    admin_locked = models.BooleanField(default=False)
    admin_lock_reason = models.CharField(max_length=255, blank=True, default='')
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='locked_listings')
    locked_at = models.DateTimeField(null=True, blank=True)

    course_name = models.CharField(max_length=255, blank=True, default='')
    professor_name = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Every public feed (listing list, recent books, search facets,
            # book page) filters on region + status='active' and sorts by
            # recency; the FK indexes alone still meant a sort over every
            # listing in the region.
            models.Index(fields=['region', 'status', '-created_at'], name='listing_region_status_idx'),
            # Search facets group active listings by category and by course
            # (search.views.BookSearchView / CourseListView).
            models.Index(fields=['region', 'status', 'category'], name='listing_region_status_cat_idx'),
            models.Index(fields=['region', 'status', 'course_name'], name='listing_region_status_crs_idx'),
            # The sitemap pages through a region's active listings in id
            # order (listings/sitemap.py).
            models.Index(fields=['region', 'status', 'id'], name='listing_region_status_id_idx'),
            # UPPER(column), matching icontains — see catalog.models.Book.
            GinIndex(OpClass(Upper('course_name'), name='gin_trgm_ops'), name='listing_course_upper_trgm_idx'),
            GinIndex(OpClass(Upper('professor_name'), name='gin_trgm_ops'), name='listing_prof_upper_trgm_idx'),
        ]

    def __str__(self):
        return f"Listing {self.id} for {self.book.title} by {self.seller.email}"

    def delete(self, *args, **kwargs):
        """Delete the listing record and, once that commits, its R2 photos.

        Uploaded photos are removed from object storage (R2 or local
        FileSystemStorage) so orphaned files don't accumulate. The removal
        waits for the commit: a delete inside a transaction that then rolls
        back (an admin deletion cancelling open orders first, say) must not
        leave a listing whose photos are already gone. Outside a transaction
        on_commit runs at once. Failures are logged, never raised — a stale
        object in the bucket is less harmful than an undeletable listing row.
        """
        photos = list(self.photos or [])
        listing_id = self.id
        result = super().delete(*args, **kwargs)
        # A photo another listing still shows is not this one's to remove.
        # Listings could once adopt any permanent photo URL, so rows that
        # share one may exist from before that was refused.
        photos = [
            url for url in photos
            if not Listing.objects.filter(photos__icontains=url).exists()
        ]
        if photos:
            transaction.on_commit(lambda: _delete_listing_photos(photos, listing_id))
        return result


def _delete_listing_photos(photo_urls, listing_id):
    for photo_url in photo_urls:
        try:
            # Photo URLs have the form https://<custom_domain>/listings/<uuid>.<ext>;
            # the R2 key is the path, listings/<uuid>.<ext>.
            key = urlparse(photo_url).path.lstrip('/')
            # Local dev serves uploads under MEDIA_URL; that prefix is
            # part of the URL, not of the storage key, and leaving it
            # on made every dev-mode delete a silent miss.
            media_url = (settings.MEDIA_URL or '/').strip('/')
            if media_url and key.startswith(media_url + '/'):
                key = key[len(media_url) + 1:]
            # Only listing photos: a chat or ad image a listing pointed at
            # belongs to someone else.
            if not key.startswith('listings/'):
                logger.warning("Skipped non-listing key %s on listing %s", key, listing_id)
                continue
            default_storage.delete(key)
            logger.info("Deleted photo %s for listing %s", key, listing_id)
        except Exception:
            logger.warning(
                "Failed to delete photo %s for listing %s (listing will be deleted anyway)",
                photo_url, listing_id,
                exc_info=True,
            )


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
    # Only the listing's own region: its lists and book pages are the only
    # ones it appears in.
    bump_cache_version('listing_list', region_code=instance.region_id)
    bump_cache_version(f'listing:{instance.pk}')
    # A book's detail page embeds its listings, so a sold or deleted listing
    # must invalidate it too — otherwise buyers keep seeing, and messaging
    # sellers about, an item that is already gone. Deliberately one
    # generation per region rather than per book; see
    # catalog.views.BookDetailView.
    bump_cache_version('book_detail', region_code=instance.region_id)

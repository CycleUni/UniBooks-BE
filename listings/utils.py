import logging
import re

from django.core.files.storage import default_storage
from rest_framework import serializers

from core.uploads import r2_client_and_options, storage_key_from_url

logger = logging.getLogger(__name__)

# Pending uploads are namespaced by uploader. Nothing else records who
# uploaded a file that isn't attached to a listing yet, so the uploader's id
# has to live in the key itself — that's what lets ListingUploadDeleteView
# prove ownership of an unattached object instead of failing open.
# Still under `tmp/`, so R2's `tmp/` lifecycle rule keeps reaping it.
TMP_LISTING_PREFIX = "tmp/listings"
PERMANENT_LISTING_PREFIX = "listings"

# Accepts the current user-scoped form and the legacy un-scoped form, so keys
# issued before this change can still be identified (they're rejected as
# unattributable by promote_tmp_photos, but recognising them keeps the error
# specific instead of falling through to "not a tmp key at all").
_TMP_KEY_RE = re.compile(
    rf'^{TMP_LISTING_PREFIX}/(?:(?P<user_id>\d+)/)?(?P<name>[^/]+)$'
)


def tmp_key_prefix(user_id):
    """Key prefix that uploads by `user_id` are written under."""
    return f"{TMP_LISTING_PREFIX}/{user_id}/"


def promote_tmp_photos(photo_urls, user_id, request=None, kept_urls=()):
    """Move any `tmp/` photos into their permanent keys and return the URLs
    rewritten to match.

    A URL that is already permanent is accepted only if it is in `kept_urls`
    — the photos the listing already has. Anything else is someone else's
    photo: adopting it let a seller delete it through
    ListingUploadDeleteView (which trusts the seller's own listings), or by
    deleting their own listing.

    Raises ValidationError rather than degrading, in every failure mode:

    - A tmp key that isn't the caller's own is refused, so a listing can't
      adopt (and, via the copy+delete below, destroy) someone else's pending
      upload.
    - A failed copy aborts the write. Keeping the tmp URL would be silent
      data loss: R2's lifecycle rule deletes `tmp/` after 7 days, so the
      listing would show a working photo now and a broken one next week.
    """
    if not photo_urls:
        return photo_urls

    client, options = r2_client_and_options()
    updated_urls = []

    for url in photo_urls:
        key = storage_key_from_url(url, request)
        if key is None:
            # Host already vetted by ListingSerializer.validate_photos; a
            # miss here means the URL doesn't point at our storage at all.
            raise serializers.ValidationError('listing.errInvalidPhotos')

        match = _TMP_KEY_RE.match(key)
        if not match:
            if url not in kept_urls:
                logger.warning(
                    "Refused permanent photo %s for user %s (not on this listing)", key, user_id
                )
                raise serializers.ValidationError('listing.errPhotoNotOwned')
            updated_urls.append(url)  # already on this listing, leave alone
            continue

        if match.group('user_id') != str(user_id):
            logger.warning(
                "Refused to promote tmp key %s for user %s (not the uploader)", key, user_id
            )
            raise serializers.ValidationError('listing.errPhotoNotOwned')

        new_key = f"{PERMANENT_LISTING_PREFIX}/{match.group('name')}"
        try:
            if client and options:
                client.copy_object(
                    Bucket=options["bucket_name"],
                    CopySource={'Bucket': options["bucket_name"], 'Key': key},
                    Key=new_key,
                )
                client.delete_object(Bucket=options["bucket_name"], Key=key)
            else:
                if not default_storage.exists(key):
                    raise FileNotFoundError(key)
                with default_storage.open(key, 'rb') as f:
                    # save() returns the name actually used, which differs
                    # from new_key if something already occupies it — build
                    # the URL from the real name, not the requested one.
                    new_key = default_storage.save(new_key, f)
                default_storage.delete(key)
        except Exception:
            logger.exception("Failed to promote tmp photo %s", key)
            raise serializers.ValidationError('listing.errPhotoPromoteFailed')

        updated_urls.append(url.replace(key, new_key, 1))

    return updated_urls


def delete_listing(listing):
    """Delete a listing, then its book if nothing else references it.

    The post_delete signal invalidates the caches. A book left with zero
    listings and zero subscriptions is an orphan and goes too, so the catalog
    doesn't accumulate dead entries.
    """
    from django.db.models import Count
    from catalog.models import Book

    book_id = listing.book_id
    listing.delete()
    (
        Book.objects
        .filter(id=book_id)
        .annotate(listing_count=Count('listings'), subscription_count=Count('subscriptions'))
        .filter(listing_count=0, subscription_count=0)
        .delete()
    )

"""Folding one Book row into another.

Used when a book turns out to be filed under the wrong ISBN and the right one
already has its own row in the region: the misfiled row's listings and
subscriptions move over and the row goes away. Callers run this inside
transaction.atomic(), so a failure part way leaves both books as they were.
"""

from core.cache import safe_cache_delete


def merge_book_into(src, dst):
    """Move everything pointing at `src` onto `dst`, then delete `src`.

    `dst` keeps its own title, authors and the rest: it is the row other
    sellers and subscribers already rely on. Returns what was moved and
    dropped, enough for an audit record to undo the merge by hand.

    Callers lock both rows first (select_for_update): a listing or
    subscription added to `src` part way through would otherwise be deleted
    with it.
    """
    if src.pk == dst.pk or src.region_id != dst.region_id:
        raise ValueError('can only merge two different books of one region')

    # One save per listing rather than a queryset update(): the post_save
    # signal is what invalidates the listing, list and book page caches.
    listing_ids = []
    for listing in src.listings.all():
        listing.book = dst
        listing.save(update_fields=['book'])
        listing_ids.append(str(listing.pk))

    # (user, book) is unique: someone subscribed to both keeps the one on dst.
    already = set(dst.subscriptions.values_list('user_id', flat=True))
    duplicates = src.subscriptions.filter(user_id__in=already)
    dropped = [
        {
            'id': str(sub.pk), 'user_id': sub.user_id, 'school_id': sub.school_id,
            'created_at': sub.created_at.isoformat(),
            'notified_at': sub.notified_at.isoformat() if sub.notified_at else None,
        }
        for sub in duplicates
    ]
    duplicates.delete()
    moved_subscription_ids = [str(pk) for pk in src.subscriptions.values_list('pk', flat=True)]
    src.subscriptions.update(book=dst)

    src.delete()
    # update() skips the subscription signals that clear this aggregate.
    safe_cache_delete('home_waitlist')
    return {
        'listing_ids': listing_ids,
        'moved_subscription_ids': moved_subscription_ids,
        'dropped_subscriptions': dropped,
    }

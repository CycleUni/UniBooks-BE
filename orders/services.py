"""Order operations shared by more than one view."""

from django.db import transaction

from core.models import AuditEvent

# Same floor the admin console has always enforced for a written reason.
PLATFORM_CANCEL_REASON_MIN_LENGTH = 3

FINAL_ORDER_STATUSES = ('cancelled', 'completed')


def lock_order(order):
    """Lock `order` and its listing for a status change; return a fresh copy.

    Must run inside transaction.atomic(). The listing is locked first and the
    order second, everywhere, so two writers can never hold one lock each and
    wait on the other. Checking a transition against the caller's unlocked
    copy let a buyer's cancel and a seller's handover both pass validation
    and both commit — leaving a handed-over order on an active listing.
    """
    from listings.models import Listing
    from .models import Order

    if order.listing_id is not None:
        # Evaluated for the lock alone; the order below re-reads the listing.
        list(Listing.objects.select_for_update().filter(pk=order.listing_id))
    return Order.objects.select_for_update().get(pk=order.pk)


def release_reservation(order, old_status):
    """Put the listing back on sale after `order`, which held it, is cancelled.

    Only a reservation the order actually held is undone: a pending order
    never reserved anything, and a seller who has since marked the listing
    sold or removed must not have it flipped back to active.
    """
    listing = order.listing
    if listing is None:
        return
    if old_status in ('accepted', 'handed_over') and listing.status == 'reserved':
        listing.status = 'active'
        listing.save(update_fields=['status'])


def platform_cancel_order(order, reason, actor):
    """Cancel `order` on the platform's authority; return the cancelled row.

    Used by the admin force-cancel and by a platform deletion of a listing
    that still has open orders. Returns None when the order had already been
    completed or cancelled by the time its lock was taken. The chat notice is
    not sent here: callers send it with notify_platform_cancel once their
    transaction has committed, so a rollback never leaves the parties told
    about a cancellation that did not happen.
    """
    with transaction.atomic():
        locked = lock_order(order)
        if locked.status in FINAL_ORDER_STATUSES:
            return None
        old_status = locked.status
        locked.status = 'cancelled'
        locked.cancel_reason = f'admin_override: {reason}'
        locked.save(update_fields=['status', 'cancel_reason', 'updated_at'])
        release_reservation(locked, old_status)

        AuditEvent.objects.create(
            user=actor,
            kind='admin.order_force_cancelled',
            meta={'order_id': str(locked.id), 'reason': reason},
        )
    return locked


def notify_platform_cancel(order, actor):
    from orders.views import send_order_notification

    send_order_notification(order, 'order.notify.admin_cancelled', sender=actor)

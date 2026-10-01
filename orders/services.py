"""Order operations shared by more than one view."""

from core.models import AuditEvent

# Same floor the admin console has always enforced for a written reason.
PLATFORM_CANCEL_REASON_MIN_LENGTH = 3


def platform_cancel_order(order, reason, actor):
    """Cancel `order` on the platform's authority and tell the parties.

    Used by the admin force-cancel and by a platform deletion of a listing
    that still has open orders. The caller checks the order is not final and
    the reason long enough; this does the cancelling, the chat notice and the
    audit record, so both paths leave the same trail.
    """
    from orders.views import send_order_notification

    order.status = 'cancelled'
    order.cancel_reason = f'admin_override: {reason}'
    order.save(update_fields=['status', 'cancel_reason', 'updated_at'])

    send_order_notification(order, 'order.notify.admin_cancelled', sender=actor)

    AuditEvent.objects.create(
        user=actor,
        kind='admin.order_force_cancelled',
        meta={'order_id': str(order.id), 'reason': reason},
    )

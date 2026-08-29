from core.region import get_region
import logging

from django.conf import settings
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from messaging.models import Conversation

from ..models import Order
from ..serializers import OrderSerializer, OrderStatusUpdateSerializer

logger = logging.getLogger(__name__)


def _post_edge_chat_message(conv, sender, msg_body, log_prefix):
    """JWT-sign and POST a chat message to the CFEdgeChat Durable Object.

    Shared by send_order_notification and OrderViewSet.perform_create so
    there is exactly one place that builds the JWT and performs the webhook
    call. Never raises: a failure here must not fail the caller's primary
    operation (order creation / status update), it is only logged.
    """
    edge_chat_url = getattr(settings, 'EDGE_CHAT_URL', 'http://localhost:8787')
    jwt_secret = getattr(settings, 'EDGE_CHAT_JWT_SECRET', '')
    if not (edge_chat_url and jwt_secret):
        return

    try:
        import urllib.request
        import urllib.error
        import json
        from rest_framework_simplejwt.backends import TokenBackend
        app_id = getattr(settings, 'EDGE_CHAT_APP_ID', 'unibooks')
        token = TokenBackend(algorithm="HS256", signing_key=jwt_secret).encode({
            "user_id": str(sender.id),
            "room_id": str(conv.id),
            "app_id": app_id,
            "participant_ids": [str(conv.buyer_id), str(conv.listing.seller_id)],
            "role": "system",
        })
        url = f"{edge_chat_url}/api/{app_id}/{conv.id}/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps({"content": msg_body}).encode('utf-8'),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                # cfedgechaft.workers.dev blocks bot-like requests
                # (Cloudflare error 1010). A proper User-Agent avoids that.
                "User-Agent": "UniBooks-BE/1.0",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status >= 400:
            body = resp.read().decode('utf-8', errors='replace')[:500]
            logger.error("[%s] POST %s -> HTTP %s body: %s", log_prefix, url, resp.status, body)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:500] if e.fp else ''
        logger.error("[%s] POST %s -> HTTP %s body: %s", log_prefix, url, e.code, body)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.error("[%s] POST %s -> network error: %s", log_prefix, url, e)
    except Exception:
        logger.exception("[%s] POST %s -> unexpected error", log_prefix, url)


def send_order_notification(order, message_key, sender=None, recipient=None):
    """Send a system notification message for order status changes.

    Args:
        order: The Order instance
        message_key: Frontend i18n key for the notification (e.g., 'order.notify.seller_approved')
        sender: User who triggered the action (if None, uses order.buyer)
        recipient: User to send to (if None, sends to the other party)
    """
    try:
        conv = Conversation.objects.get(listing=order.listing, buyer=order.buyer)
    except Conversation.DoesNotExist:
        return  # No conversation yet (shouldn't happen for meetup)

    # Determine sender: use the user who performed the action, or default to buyer
    if sender is None:
        sender = order.buyer

    # Determine recipient: the other party
    if recipient is None:
        recipient = order.seller if sender == order.buyer else order.buyer

    # Get the conversation for this order
    try:
        # Create system message with i18n key
        msg_body = f"[SYSTEM:{message_key}] System Notification"
        conv.latest_message_body = msg_body
        conv.save(update_fields=['latest_message_body', 'updated_at'])

        _post_edge_chat_message(conv, sender, msg_body, log_prefix='OrderNotify')
    except Exception:
        logger.exception("[OrderNotify] Failed to send notification")


from core.permissions import IsVerifiedInRegion

class OrderViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsVerifiedInRegion]

    def get_target_region(self, request):
        if request.method == 'POST':
            listing_id = request.data.get('listing')
            if listing_id:
                from listings.models import Listing
                listing = Listing.objects.filter(id=listing_id).first()
                if listing:
                    return listing.region
        return None

    # Which party is allowed to trigger each status transition. Transitions not
    # listed here are illegal state-machine edges and are already rejected by
    # OrderStatusUpdateSerializer.validate_status, so no role is needed for them.
    TRANSITION_ACTOR = {
        ('pending', 'accepted'): 'seller',      # seller approves the meetup request
        ('pending', 'cancelled'): 'either',     # buyer withdraws, or seller declines
        ('accepted', 'handed_over'): 'seller',  # seller marks the item as handed over
        ('accepted', 'cancelled'): 'either',    # either party backs out before handover
        ('handed_over', 'completed'): 'buyer',  # buyer confirms receipt
        ('handed_over', 'cancelled'): 'either', # either party cancels after handover
    }

    def get_serializer_class(self):
        if self.action in ['partial_update', 'update']:
            return OrderStatusUpdateSerializer
        return OrderSerializer

    def get_queryset(self):
        user = self.request.user
        region = get_region(self.request)
        qs = Order.objects.filter(Q(buyer=user) | Q(seller=user), region=region).order_by('-created_at')
        
        q = self.request.query_params.get('q')
        if q:
            q_clean = q.lstrip('#').strip()
            qs = qs.filter(
                Q(id__icontains=q_clean) |
                Q(listing__book__title__icontains=q) |
                Q(listing__book__authors__icontains=q) |
                Q(listing__book__isbn13__icontains=q)
            )
        return qs

    def _check_transition_permission(self, order, new_status, user):
        """Enforce who may trigger a given status transition (buyer/seller/either).

        Returns a 403 Response if the transition is not permitted for `user`,
        or None if it's allowed (or not a recognised transition, in which case
        the serializer's own state-machine validation will reject it).
        """
        required_role = self.TRANSITION_ACTOR.get((order.status, new_status))
        if required_role is None:
            return None
        if required_role == 'seller' and user != order.seller:
            return Response({"error": {"code": "order.errSellerOnly"}}, status=status.HTTP_403_FORBIDDEN)
        if required_role == 'buyer' and user != order.buyer:
            return Response({"error": {"code": "order.errBuyerOnly"}}, status=status.HTTP_403_FORBIDDEN)
        if required_role == 'either' and user not in (order.buyer, order.seller):
            return Response({"error": {"code": "order.errNotParticipant"}}, status=status.HTTP_403_FORBIDDEN)
        return None

    def update(self, request, *args, **kwargs):
        new_status = request.data.get('status')
        if new_status:
            instance = self.get_object()
            denied = self._check_transition_permission(instance, new_status, request.user)
            if denied is not None:
                return denied
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        listing = serializer.validated_data['listing']

        # In the new flow, we don't reserve immediately on request, wait for seller to accept.
        region = get_region(self.request)
        order = serializer.save(
            buyer=self.request.user,
            seller=listing.seller,
            total_amount=listing.price,
            status='pending',
            region=region,
            currency=region.currency
        )

        conv, _ = Conversation.objects.get_or_create(
            listing=listing,
            buyer=order.buyer
        )
        msg_body = "[SYSTEM:order.notify.meetup_requested] System Notification"
        conv.latest_message_body = msg_body
        conv.save(update_fields=['latest_message_body', 'updated_at'])

        # Sync message to CFEdgeChat Durable Object so it appears in history and triggers WebSocket updates
        _post_edge_chat_message(conv, order.buyer, msg_body, log_prefix='MeetupMsg')

    def perform_update(self, serializer):
        old_status = serializer.instance.status if serializer.instance else None
        order = serializer.save()
        new_status = order.status

        # Handle listing status changes
        listing_status_changed = False
        if new_status == 'cancelled':
            order.listing.status = 'active'
            order.listing.save(update_fields=['status'])
            listing_status_changed = True
        elif new_status == 'accepted':
            order.listing.status = 'reserved'
            order.listing.save(update_fields=['status'])
            listing_status_changed = True
        elif new_status == 'completed':
            order.listing.status = 'sold'
            order.listing.save(update_fields=['status'])
            listing_status_changed = True

        # No cache bookkeeping needed here: the listing.save() calls above fire
        # post_save, and listings.models.invalidate_listing_caches bumps every
        # affected generation (detail, lists, and the book page).

        if old_status != new_status:
            self._send_meetup_notification(order, old_status, new_status)

    def _send_meetup_notification(self, order, old_status, new_status):
        """Send appropriate notification based on status transition."""
        user = self.request.user

        # Map of status transitions to notification keys
        # Format: (old_status, new_status, sender) -> (message_key, sender_override)
        transitions = {
            # Seller approves meetup: pending -> accepted
            ('pending', 'accepted', order.seller): ('order.notify.seller_approved', order.seller),

            # Buyer cancels while pending
            ('pending', 'cancelled', order.buyer): ('order.notify.cancelled_by_buyer', order.buyer),

            # Seller rejects/cancels while pending
            ('pending', 'cancelled', order.seller): ('order.notify.seller_rejected', order.seller),

            # Buyer cancels after seller approved (accepted)
            ('accepted', 'cancelled', order.buyer): ('order.notify.cancelled_by_buyer', order.buyer),

            # Seller cancels after approving (accepted)
            ('accepted', 'cancelled', order.seller): ('order.notify.cancelled_by_seller', order.seller),

            # Seller confirms handed over
            ('accepted', 'handed_over', order.seller): ('order.notify.delivered', order.seller),

            # Buyer confirms completed
            ('handed_over', 'completed', order.buyer): ('order.notify.delivered', order.buyer),

            # Either party cancels during handed_over
            ('handed_over', 'cancelled', order.buyer): ('order.notify.cancelled_by_buyer', order.buyer),
            ('handed_over', 'cancelled', order.seller): ('order.notify.cancelled_by_seller', order.seller),
        }

        key = (old_status, new_status, user)
        if key in transitions:
            message_key, sender = transitions[key]
            send_order_notification(order, message_key, sender=sender)

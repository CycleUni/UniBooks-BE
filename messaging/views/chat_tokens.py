import datetime
import hmac
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from rest_framework import views, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.backends import TokenBackend

from messaging.models import Conversation

logger = logging.getLogger(__name__)

# Preview strings that are not literal message text: CFEdgeChat sends these
# i18n placeholder tokens for image messages and for system/order
# notifications, and the frontend resolves them per viewer. An email has no
# such resolver, so a preview starting with one of these is dropped rather
# than pasted in raw.
SYSTEM_PREVIEW_PREFIXES = ("[SYSTEM:", "[MEETUP_")

# How much of a message the notification email quotes. Enough to recognise
# the conversation, short enough that the email is a nudge to open the app
# rather than a copy of the chat.
CHAT_EMAIL_PREVIEW_MAX_CHARS = 120


class ChatTokenView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.EDGE_CHAT_JWT_SECRET:
            return Response({"error": "Chat not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        conversation_id = request.query_params.get('conversation_id')
        if not conversation_id:
            return Response({"error": {"code": "msg.errConversationRequired"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if conversation.buyer_id != request.user.id and conversation.listing.seller_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)

        # room_id ties this token to exactly this conversation: CFEdgeChat
        # rejects any attempt to use it against a different room, so a valid
        # token can't be replayed to read/send in someone else's conversation.
        #
        # A standalone TokenBackend instance (not settings.SIMPLE_JWT) is
        # used to sign with EDGE_CHAT_JWT_SECRET instead of the app's own JWT
        # key: rest_framework_simplejwt caches api_settings.SIGNING_KEY on
        # first use per-process, so temporarily mutating settings.SIMPLE_JWT
        # here would silently no-op after the first login/refresh in the
        # process and sign with the wrong key.
        token_backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)
        token = token_backend.encode({
            "user_id": str(request.user.id),
            "room_id": str(conversation.id),
            # Must match the `appId` URL segment CFEdgeChat validates the
            # room-scoped request/connection against (`/ws/<app_id>/<room_id>`,
            # `/api/<app_id>/<room_id>/...`) — its ChatRoom DO is keyed by
            # `${appId}:${roomId}`, and rejects (403) any token whose app_id
            # doesn't match the path exactly. See settings.EDGE_CHAT_APP_ID.
            "app_id": settings.EDGE_CHAT_APP_ID,
            # Lets CFEdgeChat's ChatRoom DO learn, from the signed token itself
            # rather than trusting client input, who is allowed to receive
            # cross-room notifications about this conversation on their
            # single per-user hub connection.
            "participant_ids": [str(conversation.buyer_id), str(conversation.listing.seller_id)],
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
        })
        edge_chat_url = getattr(settings, 'EDGE_CHAT_URL', 'http://localhost:8787')
        return Response({"token": token, "edge_chat_url": edge_chat_url})


class HubTokenView(views.APIView):
    # A single, room-independent token for the per-user notification hub
    # (one WebSocket per user covering all of their conversations, instead of
    # one per open chat). It only proves identity — it carries no room_id and
    # grants no access to any conversation's content, since CFEdgeChat's
    # UserHub only relays events that ChatRoom DOs push to it (see
    # ChatRoom's use of `participant_ids` above), never anything a client
    # requests by room_id over this connection.
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.EDGE_CHAT_JWT_SECRET:
            return Response({"error": "Chat not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        token_backend = TokenBackend(algorithm="HS256", signing_key=settings.EDGE_CHAT_JWT_SECRET)
        token = token_backend.encode({
            "user_id": str(request.user.id),
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
        })
        edge_chat_url = getattr(settings, 'EDGE_CHAT_URL', 'http://localhost:8787')
        return Response({"token": token, "edge_chat_url": edge_chat_url})


def _chat_email_preview(preview):
    """The quotable part of a message preview, or None if there isn't one."""
    text = (preview or "").strip()
    if not text or text.startswith(SYSTEM_PREVIEW_PREFIXES):
        return None
    # Collapse newlines: the preview goes into a one-line "Message:" field,
    # and a multi-line paste would run into the link below it.
    text = " ".join(text.split())
    if len(text) > CHAT_EMAIL_PREVIEW_MAX_CHARS:
        text = text[:CHAT_EMAIL_PREVIEW_MAX_CHARS].rstrip() + "…"
    return text


class EdgeChatWebhookView(views.APIView):
    # Server-to-server callback from the CFEdgeChat Worker: not a logged-in
    # user, so DRF's normal JWT auth doesn't apply. Authenticity is instead
    # verified via a shared secret (X-Webhook-Secret header, compared with
    # settings.EDGE_CHAT_WEBHOOK_SECRET using a timing-safe comparison) —
    # see _verify_webhook_secret below. If the secret isn't configured at
    # all, the endpoint refuses every request rather than accepting
    # unauthenticated calls.
    #
    # Read-state (`{buyer,seller}_last_read_at`) is NO LONGER updated here.
    # Mark-read is owned by CFEdgeChat's UserHub (see
    # `POST /api/<app>/<room>/read`).
    #
    # Two events share this URL, because the Worker has a single
    # DJANGO_WEBHOOK_URL to point at:
    #   - the default (unlabelled) one mirrors the conversation's
    #     latest_message_body + updated_at into the Django row so the inbox
    #     listing (order_by('-updated_at')) and listing-detail preview stay
    #     in sync;
    #   - "offline_email" asks for a notification email to a participant who
    #     has no connection to CFEdgeChat at all (see _handle_offline_email).
    permission_classes = [AllowAny]

    def _verify_webhook_secret(self, request):
        """Timing-safe shared-secret check. Returns an error Response, or None if OK."""
        expected = settings.EDGE_CHAT_WEBHOOK_SECRET
        if not expected:
            return Response(
                {"error": {"code": "msg.errWebhookNotConfigured"}},
                status=status.HTTP_403_FORBIDDEN,
            )
        provided = request.headers.get("X-Webhook-Secret", "")
        if not provided or not hmac.compare_digest(provided, expected):
            return Response(
                {"error": {"code": "msg.errWebhookSecretInvalid"}},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def post(self, request):
        auth_error = self._verify_webhook_secret(request)
        if auth_error is not None:
            return auth_error

        data = request.data

        # Two kinds of call arrive on this one URL (CFEdgeChat has a single
        # DJANGO_WEBHOOK_URL to configure). The default, unlabelled event is
        # the inbox-preview mirror below; "offline_email" comes from the
        # UserHub of a recipient with no live connection to the Worker.
        if data.get("event") == "offline_email":
            return self._handle_offline_email(data)

        # Example payload: {"room_id": "...", "sender_id": "...", "content": "...", "timestamp": ..., "is_offline": true/false}
        room_id = data.get("room_id")
        sender_id = data.get("sender_id")
        content = data.get("content")
        is_offline = data.get("is_offline", False)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=room_id)
            conversation.latest_message_body = content
            conversation.save(update_fields=['latest_message_body', 'updated_at'])

            if is_offline:
                # "Offline" here only means nobody else had *this room* open.
                # Email notification is driven by the separate offline_email
                # event, which fires on the stricter condition (the recipient
                # has no connection to CFEdgeChat at all).
                # Message content is deliberately excluded from this log line.
                logger.info("Offline message in room %s from %s", room_id, sender_id)

            return Response({"status": "received"}, status=status.HTTP_200_OK)

        except (Conversation.DoesNotExist, ValidationError):
            # ValidationError covers a malformed (non-UUID) room_id
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    def _handle_offline_email(self, data):
        """Email a participant about a message that arrived while they were away.

        Payload: {"event": "offline_email", "room_id", "recipient_id",
                  "sender_id", "preview", "timestamp"}.

        CFEdgeChat decides *when* to ask (recipient has no WebSocket on their
        UserHub, and this conversation hasn't already been emailed about since
        they last opened it — see UserHub.maybeSendOfflineEmail). This side
        decides *whether the person should be emailed at all*, which is the
        part that needs the database: they may have deactivated their account
        or deleted this conversation, in which case the mail would point at
        something they cannot open.
        """
        room_id = data.get("room_id")
        recipient_id = data.get("recipient_id")

        if not recipient_id:
            return Response({"error": "recipient_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = (
                Conversation.objects
                .select_related("listing__book", "listing__seller", "buyer")
                .get(id=room_id)
            )
        except (Conversation.DoesNotExist, ValidationError):
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        buyer = conversation.buyer
        seller = conversation.listing.seller
        if str(buyer.id) == str(recipient_id):
            recipient, other, hidden_at = buyer, seller, conversation.buyer_deleted_at
        elif str(seller.id) == str(recipient_id):
            recipient, other, hidden_at = seller, buyer, conversation.seller_deleted_at
        else:
            # The hub that asked isn't a participant of this room. Can only
            # happen if the two sides disagree about the conversation, so it
            # is a bug report rather than a routine skip.
            logger.warning("Offline email for room %s names a non-participant recipient", room_id)
            return Response({"error": "Not a participant"}, status=status.HTTP_400_BAD_REQUEST)

        if hidden_at is not None:
            # They deleted this conversation: it stays out of their inbox even
            # though new messages keep arriving in the room, so the link would
            # lead nowhere they can see.
            return Response({"status": "skipped", "reason": "conversation_deleted"}, status=status.HTTP_200_OK)
        if recipient.deleted_at is not None or not recipient.is_active or not recipient.email:
            return Response({"status": "skipped", "reason": "recipient_unreachable"}, status=status.HTTP_200_OK)

        self._send_chat_notification_email(conversation, recipient, other, data.get("preview"))
        return Response({"status": "sent"}, status=status.HTTP_200_OK)

    def _send_chat_notification_email(self, conversation, recipient, sender, preview):
        """Best-effort send. A mail failure must not turn into a 500 for the
        Worker, which has already recorded that this conversation was
        notified and will not ask again until the recipient opens it.

        Bilingual (zh-TW then English) like the waitlist mail in cron.views:
        there is no request to resolve a language from here, and no stored
        per-user language preference to read.
        """
        # Header values must not contain newlines, and display_name is
        # user-supplied; Django raises BadHeaderError rather than sending, but
        # only after the caller has already been told the mail went out.
        sender_name = " ".join((sender.display_name or "").split()) or "UniBooks"
        listing_title = conversation.listing.book.title if conversation.listing.book else ""
        # Region-prefixed: every frontend route lives under /<region>/, and the
        # inbox only lists conversations belonging to the region currently
        # selected — a link without the prefix lands the reader in whichever
        # region they last used, where this conversation may not exist.
        region = str(conversation.listing.region_id).lower()
        link = f"{settings.FRONTEND_URL}/{region}/messages?chat={conversation.id}"
        quoted = _chat_email_preview(preview)

        subject = f"UniBooks 新訊息 / New message from {sender_name}"
        zh_lines = [f"{sender_name} 在 UniBooks 傳送了新訊息給您。", ""]
        en_lines = [f"{sender_name} sent you a new message on UniBooks.", ""]
        if listing_title:
            zh_lines.append(f"書籍：{listing_title}")
            en_lines.append(f"Listing: {listing_title}")
        if quoted:
            zh_lines.append(f"訊息：{quoted}")
            en_lines.append(f"Message: {quoted}")
        zh_lines += ["", f"前往查看：{link}", "", "在您開啟這則對話之前，同一則對話的後續訊息不會再寄送通知信。"]
        en_lines += ["", f"Open the conversation: {link}", "",
                     "You won't get another email about this conversation until you open it."]
        message = "\n".join(zh_lines) + "\n\n---\n\n" + "\n".join(en_lines)

        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                fail_silently=False,
            )
        except Exception:
            # Neither the message text nor the address is logged: the first is
            # private conversation content, the second is PII.
            logger.exception("Failed to send chat notification for room %s", conversation.id)

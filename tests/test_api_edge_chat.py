"""API tests for the CFEdgeChat integration surface: chat-token issuance and
the offline-message webhook (messaging.views.ChatTokenView / EdgeChatWebhookView)."""

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.utils import timezone

from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation

User = get_user_model()

PASSWORD = "test-only-password-123"
FAKE_SECRET = "test-only-edge-chat-secret"
FAKE_WEBHOOK_SECRET = "test-only-edge-chat-webhook-secret"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    u = User.objects.create_user(
        email="chat-user@example.com", first_name="Chat", last_name="User", password=PASSWORD
    )
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


@pytest.fixture
def auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def conversation(db, user):
    # `user` is the buyer; a separate seller owns the listing being chatted about.
    seller = User.objects.create_user(
        email="chat-seller@example.com", first_name="Chat", last_name="Seller", password=PASSWORD
    )
    book = Book.objects.create(region_id='TW', title="Chat Test Book", source="manual")
    listing = Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new")
    return Conversation.objects.create(listing=listing, buyer=user)


def test_chat_token_requires_auth(api, db):
    assert api.get("/api/v1/messaging/chat-token/").status_code == 401


@override_settings(EDGE_CHAT_JWT_SECRET="")
def test_chat_token_returns_500_when_not_configured(api, user, auth_header):
    resp = api.get("/api/v1/messaging/chat-token/", **auth_header)
    assert resp.status_code == 500


@override_settings(EDGE_CHAT_JWT_SECRET=FAKE_SECRET)
def test_chat_token_requires_conversation_id(api, user, auth_header):
    resp = api.get("/api/v1/messaging/chat-token/", **auth_header)
    assert resp.status_code == 400


@override_settings(EDGE_CHAT_JWT_SECRET=FAKE_SECRET)
def test_chat_token_rejects_non_participant(api, user, auth_header, conversation, db):
    outsider = User.objects.create_user(
        email="chat-outsider@example.com", first_name="Out", last_name="Sider", password=PASSWORD
    )
    outsider_header = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(outsider)['access']}"}
    resp = api.get(
        f"/api/v1/messaging/chat-token/?conversation_id={conversation.id}", **outsider_header
    )
    assert resp.status_code == 403


@override_settings(EDGE_CHAT_JWT_SECRET=FAKE_SECRET, EDGE_CHAT_URL="http://edge-chat.invalid:8787")
def test_chat_token_issues_verifiable_jwt(api, user, auth_header, conversation):
    resp = api.get(
        f"/api/v1/messaging/chat-token/?conversation_id={conversation.id}", **auth_header
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["edge_chat_url"] == "http://edge-chat.invalid:8787"

    # The token must be verifiable with the shared secret (same as the
    # CFEdgeChat Worker does with `jose.jwtVerify`), carry the user id, and be
    # scoped to exactly this conversation (room_id) so it can't be replayed
    # against a different conversation's room.
    payload = jwt.decode(body["token"], FAKE_SECRET, algorithms=["HS256"])
    assert payload["user_id"] == str(user.id)
    assert payload["room_id"] == str(conversation.id)
    assert "exp" in payload

    # CFEdgeChat now requires every room-scoped token to carry an app_id
    # claim matching the `appId` URL segment (its ChatRoom DO is keyed by
    # `${appId}:${roomId}`), or it rejects the request with 403. Must equal
    # settings.EDGE_CHAT_APP_ID, which in turn must match the frontend's
    # hardcoded appId ("unibooks") used to build /ws/<app_id>/<room_id>.
    assert payload["app_id"] == "unibooks"

    # CFEdgeChat's ChatRoom DO trusts this claim (not client input) to learn
    # who to notify on the per-user hub connection when a message lands.
    assert set(payload["participant_ids"]) == {
        str(conversation.buyer_id), str(conversation.listing.seller_id)
    }


@override_settings(
    EDGE_CHAT_JWT_SECRET=FAKE_SECRET,
    EDGE_CHAT_URL="http://edge-chat.invalid:8787",
    EDGE_CHAT_APP_ID="another-app",
)
def test_chat_token_app_id_follows_setting(api, user, auth_header, conversation):
    # The app_id claim must come from settings.EDGE_CHAT_APP_ID (not be
    # hardcoded), so it can be kept in sync with whatever appId the frontend
    # is actually configured/deployed with.
    resp = api.get(
        f"/api/v1/messaging/chat-token/?conversation_id={conversation.id}", **auth_header
    )
    payload = jwt.decode(resp.json()["token"], FAKE_SECRET, algorithms=["HS256"])
    assert payload["app_id"] == "another-app"


@override_settings(EDGE_CHAT_JWT_SECRET=FAKE_SECRET)
def test_chat_token_rejects_tampered_secret(api, user, auth_header, conversation):
    resp = api.get(
        f"/api/v1/messaging/chat-token/?conversation_id={conversation.id}", **auth_header
    )
    token = resp.json()["token"]
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "wrong-secret", algorithms=["HS256"])


def test_hub_token_requires_auth(api, db):
    assert api.get("/api/v1/messaging/hub-token/").status_code == 401


@override_settings(EDGE_CHAT_JWT_SECRET="")
def test_hub_token_returns_500_when_not_configured(api, user, auth_header):
    resp = api.get("/api/v1/messaging/hub-token/", **auth_header)
    assert resp.status_code == 500


@override_settings(EDGE_CHAT_JWT_SECRET=FAKE_SECRET, EDGE_CHAT_URL="http://edge-chat.invalid:8787")
def test_hub_token_issues_user_scoped_jwt(api, user, auth_header):
    resp = api.get("/api/v1/messaging/hub-token/", **auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["edge_chat_url"] == "http://edge-chat.invalid:8787"

    # No room_id / participant_ids here: this token only proves identity for
    # the single per-user hub connection, it doesn't grant access to any
    # conversation's content on its own.
    payload = jwt.decode(body["token"], FAKE_SECRET, algorithms=["HS256"])
    assert payload["user_id"] == str(user.id)
    assert "room_id" not in payload
    assert "exp" in payload


def test_edge_chat_webhook_rejects_when_not_configured(api, conversation):
    # EDGE_CHAT_WEBHOOK_SECRET unset (default "" in tests): the webhook must
    # refuse every request rather than accepting unauthenticated calls.
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {"room_id": str(conversation.id), "sender_id": "7", "content": "hello", "timestamp": 1234567890},
        content_type="application/json",
    )
    assert resp.status_code == 403


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_edge_chat_webhook_rejects_missing_secret_header(api, conversation):
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {"room_id": str(conversation.id), "sender_id": "7", "content": "hello", "timestamp": 1234567890},
        content_type="application/json",
    )
    assert resp.status_code == 403


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_edge_chat_webhook_rejects_wrong_secret_header(api, conversation):
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {"room_id": str(conversation.id), "sender_id": "7", "content": "hello", "timestamp": 1234567890},
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET="wrong-secret",
    )
    assert resp.status_code == 403


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_edge_chat_webhook_accepts_authenticated_post(api, conversation):
    # Correct shared secret: the Worker calls this server-to-server, not as a
    # logged-in user. room_id must be a real conversation id — the webhook
    # uses it to update that conversation's cached latest_message_body for
    # the inbox preview.
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {"room_id": str(conversation.id), "sender_id": "7", "content": "hello", "timestamp": 1234567890},
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"
    conversation.refresh_from_db()
    assert conversation.latest_message_body == "hello"


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_edge_chat_webhook_tolerates_missing_fields(api, db):
    # No room_id -> no matching conversation, but this must not crash (500);
    # a clean 404 is the correct response for a webhook about an unknown room.
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {},
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET,
    )
    assert resp.status_code == 404


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_edge_chat_webhook_tolerates_malformed_room_id(api, db):
    # Non-UUID room_id (e.g. before Django ever created a matching
    # conversation) must 404, not crash with an uncaught ValidationError.
    resp = api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        {"room_id": "unibooks:42", "sender_id": "7", "content": "hello", "timestamp": 1234567890},
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET,
    )
    assert resp.status_code == 404


# NOTE: mark-read / unread-count are no longer served by Django. Per
# messaging.views.EdgeChatWebhookView's docstring, read-state
# (`{buyer,seller}_last_read_at`) is owned by CFEdgeChat's UserHub
# (`POST /api/<app>/<room>/read`) — Django's own `.../conversations/<id>/read/`
# and `.../unread-count/` endpoints, along with the webhook's old
# mark-sender-as-read side effect, were intentionally removed (see commit
# da79b12). That coverage now belongs in the CFEdgeChat worker's own test
# suite, out of scope for this repo.


# --- offline_email event: notify a participant who has the site closed ------
#
# CFEdgeChat's UserHub owns the "should we ask at all" half of this (no live
# WebSocket for the recipient, and this conversation not already notified
# since they last opened it — tested in the Worker's own suite). What is
# tested here is the half Django owns: who the mail goes to, and whom it must
# not go to.


def _offline_email_payload(conversation, recipient, **overrides):
    payload = {
        "event": "offline_email",
        "room_id": str(conversation.id),
        "recipient_id": str(recipient.id),
        "sender_id": str(conversation.listing.seller_id),
        "preview": "are you free on Friday?",
        "timestamp": 1234567890,
    }
    payload.update(overrides)
    return payload


def _post_offline_email(api, payload, secret=FAKE_WEBHOOK_SECRET):
    return api.post(
        "/api/v1/messaging/webhook/edge-chat/",
        payload,
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET=secret,
    )


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_still_requires_the_shared_secret(api, conversation, user, mailoutbox):
    resp = _post_offline_email(api, _offline_email_payload(conversation, user), secret="wrong-secret")
    assert resp.status_code == 403
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_sends_to_the_recipient(api, conversation, user, mailoutbox):
    resp = _post_offline_email(api, _offline_email_payload(conversation, user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"

    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to == [user.email]
    # Enough to know which conversation it is, and a link straight into it.
    assert "Chat Test Book" in mail.body
    # Region-prefixed, matching the frontend's /<region>/... route table.
    assert f"/tw/messages?chat={conversation.id}" in mail.body
    assert "are you free on Friday?" in mail.body


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_can_go_to_the_seller_too(api, conversation, mailoutbox):
    seller = conversation.listing.seller
    resp = _post_offline_email(
        api,
        _offline_email_payload(conversation, seller, sender_id=str(conversation.buyer_id)),
    )
    assert resp.status_code == 200
    assert [m.to for m in mailoutbox] == [[seller.email]]


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_omits_system_placeholder_previews(api, conversation, user, mailoutbox):
    # Image and order-notification previews are i18n tokens the frontend
    # resolves per viewer; an email has no resolver, so the raw token must
    # never appear in one.
    resp = _post_offline_email(
        api,
        _offline_email_payload(conversation, user, preview="[SYSTEM:msg.imagePlaceholder]"),
    )
    assert resp.status_code == 200
    assert len(mailoutbox) == 1
    assert "[SYSTEM:" not in mailoutbox[0].body


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_truncates_a_long_preview(api, conversation, user, mailoutbox):
    resp = _post_offline_email(api, _offline_email_payload(conversation, user, preview="x" * 500))
    assert resp.status_code == 200
    assert "x" * 500 not in mailoutbox[0].body
    assert "…" in mailoutbox[0].body


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_skips_a_conversation_the_recipient_deleted(api, conversation, user, mailoutbox):
    # Deleting hides the conversation from their inbox for good, so the link
    # would lead to something they cannot open.
    conversation.buyer_deleted_at = timezone.now()
    conversation.save(update_fields=["buyer_deleted_at"])

    resp = _post_offline_email(api, _offline_email_payload(conversation, user))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "conversation_deleted"
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_skips_a_deactivated_recipient(api, conversation, user, mailoutbox):
    user.is_active = False
    user.save(update_fields=["is_active"])

    resp = _post_offline_email(api, _offline_email_payload(conversation, user))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "recipient_unreachable"
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_skips_a_recipient_who_turned_message_emails_off(api, conversation, user, mailoutbox):
    user.notify_new_message_email = False
    user.save(update_fields=["notify_new_message_email"])

    resp = _post_offline_email(api, _offline_email_payload(conversation, user))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "opted_out"
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_one_participant_turning_message_emails_off_does_not_silence_the_other(api, conversation, user, mailoutbox):
    user.notify_new_message_email = False
    user.save(update_fields=["notify_new_message_email"])
    seller = conversation.listing.seller

    resp = _post_offline_email(
        api,
        _offline_email_payload(conversation, seller, sender_id=str(conversation.buyer_id)),
    )
    assert resp.json()["status"] == "sent"
    assert [m.to for m in mailoutbox] == [[seller.email]]


@pytest.mark.parametrize("site_language, email_language, expect, absent", [
    # Nothing known about the user yet: the conversation's region default.
    ("", "auto", "傳送了新訊息給您", "sent you a new message"),
    # Follows the language they last used the site in.
    ("en", "auto", "sent you a new message", "新訊息"),
    ("zh-HK", "auto", "向你傳送了新訊息", "sent you a new message"),
    # An explicit choice beats the site language.
    ("en", "zh-TW", "傳送了新訊息給您", "sent you a new message"),
])
@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_is_written_in_one_language(api, conversation, user, mailoutbox, site_language, email_language, expect, absent):
    user.site_language, user.email_language = site_language, email_language
    user.save(update_fields=["site_language", "email_language"])

    _post_offline_email(api, _offline_email_payload(conversation, user))

    mail = mailoutbox[0]
    assert expect in mail.body
    assert absent not in mail.body
    assert absent not in mail.subject
    assert "---" not in mail.body


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_points_at_the_notification_settings(api, conversation, user, mailoutbox):
    _post_offline_email(api, _offline_email_payload(conversation, user))
    body = mailoutbox[0].body
    # An account page, so no region prefix; the conversation link keeps its own.
    assert f"{settings.FRONTEND_URL}/account/notifications" in body
    assert "/tw/account/" not in body


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_rejects_a_non_participant_recipient(api, conversation, db, mailoutbox):
    outsider = User.objects.create_user(
        email="chat-outsider2@example.com", first_name="Out", last_name="Sider", password=PASSWORD
    )
    resp = _post_offline_email(api, _offline_email_payload(conversation, outsider))
    assert resp.status_code == 400
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_tolerates_an_unknown_room(api, db, mailoutbox):
    resp = _post_offline_email(
        api,
        {"event": "offline_email", "room_id": "unibooks:42", "recipient_id": "7", "preview": "hi"},
    )
    assert resp.status_code == 404
    assert mailoutbox == []


@override_settings(EDGE_CHAT_WEBHOOK_SECRET=FAKE_WEBHOOK_SECRET)
def test_offline_email_does_not_touch_the_inbox_preview(api, conversation, user, mailoutbox):
    # The preview mirror is the *other* event on this URL; a notification
    # request must not rewrite latest_message_body with a truncated copy.
    conversation.latest_message_body = "the real latest message"
    conversation.save(update_fields=["latest_message_body"])

    resp = _post_offline_email(api, _offline_email_payload(conversation, user))
    assert resp.status_code == 200
    conversation.refresh_from_db()
    assert conversation.latest_message_body == "the real latest message"

"""API tests for the CFEdgeChat integration surface: chat-token issuance and
the offline-message webhook (messaging.views.ChatTokenView / EdgeChatWebhookView)."""

import jwt
import pytest
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

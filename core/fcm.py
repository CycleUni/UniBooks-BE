"""Firebase Cloud Messaging (HTTP v1) sender for browser push notifications.

Written against the REST API rather than a Google client library: the
requirements are pinned and reviewed one by one, and the whole job here is two
requests (an OAuth token, then the message), which `cryptography` and
`requests` — both already pinned — cover.

Optional feature, same posture as the other integrations in settings.py:
without FCM_PROJECT_ID and FCM_SERVICE_ACCOUNT_JSON nothing is sent and
nothing fails, so a deployment that has not created a Firebase project yet
behaves exactly as it did before push existed.
"""

import base64
import json
import logging
import threading
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Neither request has a user waiting on it as such, but both run inside the
# CFEdgeChat webhook call, which must come back before the Worker gives up.
REQUEST_TIMEOUT_SECONDS = 5
# How long FCM keeps a message for a device that is offline, in seconds. A
# chat nudge older than a day is noise.
MESSAGE_TTL_SECONDS = 24 * 60 * 60
# Refresh this long before the access token actually expires.
TOKEN_REFRESH_MARGIN_SECONDS = 300

_token_lock = threading.Lock()
_cached_token = {"value": None, "expires_at": 0.0}


def _service_account():
    """The parsed service-account key, or None when push is not configured.

    FCM_SERVICE_ACCOUNT_JSON is the key file's contents, either as is or
    base64-encoded (a multi-line private key is awkward in some env UIs).
    """
    raw = (getattr(settings, "FCM_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if not raw or not getattr(settings, "FCM_PROJECT_ID", ""):
        return None
    try:
        if not raw.startswith("{"):
            raw = base64.b64decode(raw).decode("utf-8")
        account = json.loads(raw)
        if not account.get("client_email") or not account.get("private_key"):
            raise ValueError("client_email / private_key missing")
        return account
    except Exception:
        # The key itself is never logged.
        logger.error("FCM_SERVICE_ACCOUNT_JSON is set but is not a usable service-account key")
        return None


def is_configured():
    return _service_account() is not None


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _signed_assertion(account, now):
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({
        "iss": account["client_email"],
        "scope": FCM_SCOPE,
        "aud": account.get("token_uri") or DEFAULT_TOKEN_URI,
        "iat": now,
        "exp": now + 3600,
    }).encode())
    signing_input = header + b"." + claims
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + _b64url(signature)).decode()


def _access_token(account):
    with _token_lock:
        now = time.time()
        if _cached_token["value"] and now < _cached_token["expires_at"] - TOKEN_REFRESH_MARGIN_SECONDS:
            return _cached_token["value"]
        resp = requests.post(
            account.get("token_uri") or DEFAULT_TOKEN_URI,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": _signed_assertion(account, int(now)),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        _cached_token["value"] = body["access_token"]
        _cached_token["expires_at"] = now + int(body.get("expires_in", 3600))
        return _cached_token["value"]


def _is_unregistered(resp):
    """True when FCM says the token will never work again (app uninstalled,
    permission revoked, token rotated) rather than the send merely failing."""
    if resp.status_code != 404:
        return False
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        return False
    return any(d.get("errorCode") == "UNREGISTERED" for d in details)


def send_data_message(tokens, data):
    """Send `data` (a flat str -> str dict) to each token.

    Data-only on purpose: the service worker builds the notification itself,
    so the title and body are exactly what the server wrote in the recipient's
    language. Returns (sent_count, invalid_tokens). Never raises: a push
    failure is not worth failing the webhook for.
    """
    account = _service_account()
    if account is None or not tokens:
        return 0, []

    try:
        access_token = _access_token(account)
    except Exception:
        logger.exception("FCM: could not get an access token")
        return 0, []

    url = SEND_URL.format(project_id=settings.FCM_PROJECT_ID)
    headers = {"Authorization": f"Bearer {access_token}"}
    sent, invalid = 0, []
    for token in tokens:
        body = {"message": {
            "token": token,
            "data": data,
            "webpush": {"headers": {"TTL": str(MESSAGE_TTL_SECONDS)}},
        }}
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException:
            logger.exception("FCM: send failed")
            continue
        if resp.ok:
            sent += 1
        elif _is_unregistered(resp):
            invalid.append(token)
        else:
            # The token and the message are not logged: one is a credential
            # for this device, the other private conversation context.
            logger.warning("FCM: send rejected with HTTP %s", resp.status_code)
    return sent, invalid


def push_to_user(user, *, title, body, link):
    """Push to every device `user` has registered; returns how many accepted.

    Honours the user's push switch, so every caller gets it without having to
    remember. Devices FCM reports as gone are deleted here, so the table does
    not accumulate tokens that can never be reached again.
    """
    from accounts.models import PushDevice

    if not user.notify_push:
        return 0

    tokens = list(PushDevice.objects.filter(user=user).values_list("token", flat=True))
    if not tokens:
        return 0
    sent, invalid = send_data_message(tokens, {"title": title, "body": body, "link": link})
    if invalid:
        PushDevice.objects.filter(token__in=invalid).delete()
    return sent

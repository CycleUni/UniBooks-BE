"""Single-use links mailed to users: registration, campus email, password
reset, email change (ADR 0001).

Stored as OneTimeToken rows. They used to live in the cache, where an
eviction under memory pressure silently killed a link the user had just been
mailed, and nothing recorded which links had been issued. While
settings.AUTH_LEGACY_CACHE_FALLBACK is on, links mailed before the switch
are still honoured from the cache; turn it off once the longest of them
(24 hours) has expired.
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

REGISTER = "register"
EDU_VERIFY = "edu_verify"
PASSWORD_RESET = "password_reset"
EMAIL_CHANGE = "email_change"

REGISTER_TTL = 86400
EDU_VERIFY_TTL = 86400
PASSWORD_RESET_TTL = 3600
EMAIL_CHANGE_TTL = 3600

# Cache key prefixes the links were stored under before ADR 0001.
_LEGACY_PREFIX = {
    REGISTER: "register-verify",
    EDU_VERIFY: "verify",
    PASSWORD_RESET: "password-reset",
    EMAIL_CHANGE: "email-change",
}


def _model():
    from accounts.models import OneTimeToken
    return OneTimeToken


def _legacy_enabled():
    return getattr(settings, "AUTH_LEGACY_CACHE_FALLBACK", False)


def _legacy_key(purpose, token):
    return f"{_LEGACY_PREFIX[purpose]}:{token}"


def _legacy_pending_email_key(user_id):
    return f"email-change-pending:{user_id}"


def _legacy_delete(*keys):
    for key in keys:
        try:
            cache.delete(key)
        except Exception:
            logger.exception("Legacy cache error deleting %s", key)


def issue(purpose, user_id, ttl, **payload):
    """Create a link token. `payload` travels with it (edu_email, email)."""
    token = str(uuid.uuid4())
    _model().objects.create(
        token=token,
        purpose=purpose,
        user_id=user_id,
        payload=payload,
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )
    return token


def read(purpose, token):
    """{"user_id": ..., **payload} for a live token, else None. Does not
    consume it: callers validate first and consume only on success, so a
    rejected new password (say) leaves the link usable."""
    row = (
        _model().objects
        .filter(token=token, purpose=purpose, expires_at__gt=timezone.now())
        .first()
    )
    if row is not None:
        return {"user_id": row.user_id, **row.payload}
    if _legacy_enabled():
        record = cache.get(_legacy_key(purpose, token))
        if isinstance(record, dict):
            return record
    return None


def consume(purpose, token):
    _model().objects.filter(token=token, purpose=purpose).delete()
    if _legacy_enabled():
        _legacy_delete(_legacy_key(purpose, token))


def issue_email_change(user_id, email):
    """One outstanding request per user: a new request supersedes the
    previous link rather than leaving several live at once."""
    cancel_email_change(user_id)
    return issue(EMAIL_CHANGE, user_id, EMAIL_CHANGE_TTL, email=email)


def pending_email_change(user_id):
    """The address this user has asked to move to but not yet confirmed."""
    row = (
        _model().objects
        .filter(user_id=user_id, purpose=EMAIL_CHANGE, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if row is not None:
        return row.payload.get("email")
    if _legacy_enabled():
        record = cache.get(_legacy_pending_email_key(user_id))
        if isinstance(record, dict):
            return record.get("email")
    return None


def cancel_email_change(user_id):
    _model().objects.filter(user_id=user_id, purpose=EMAIL_CHANGE).delete()
    if _legacy_enabled():
        pending_key = _legacy_pending_email_key(user_id)
        try:
            record = cache.get(pending_key)
        except Exception:
            logger.exception("Legacy cache error reading %s", pending_key)
            record = None
        if isinstance(record, dict) and record.get("token"):
            _legacy_delete(_legacy_key(EMAIL_CHANGE, record["token"]))
        _legacy_delete(pending_key)


def purge():
    """Housekeeping for the cleanup cron."""
    deleted, _ = _model().objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted

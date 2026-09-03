import logging
import time
import uuid
from datetime import timedelta
from django.core.cache import cache
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

# Refresh token TTL defaults to 14 days
REFRESH_TOKEN_LIFETIME = timedelta(days=14)

# Rotation grace period: the near-term scenario this mechanism was built for
# is two tabs refreshing with the same still-valid token within moments of
# each other, and this is how long the rotated-into pair is handed back.
#
# The cache entry itself is kept for REFRESH_TOKEN_LIFETIME, not for this
# window, so a *much later* replay is still recognizable as "already handled"
# instead of looking identical to "this jti was never issued at all" (which is
# what a cache eviction looks like, and treating that as theft was the bug
# behind users being logged out everywhere). But recognizing it and honouring
# it are different things: past the window the reply is a 401, because handing
# the current pair to anyone holding a 13-day-old rotated token is exactly the
# replay this rotation is supposed to defeat. The frontend's interceptor
# already covers the case this leaves behind — a tab that wakes with a stale
# token adopts whatever the tab that rotated it wrote to localStorage.
REFRESH_ROTATION_GRACE = timedelta(seconds=60)


def _safe_cache_get(key, default=None):
    """cache.get() that degrades to `default` on a Redis hiccup (timeout,
    connection reset) instead of raising and crashing the request. For the
    JWT whitelist, an unreachable cache is treated the same as "this token
    isn't known" — see verify_and_revoke_refresh_token for why that no
    longer escalates to revoking every other session."""
    try:
        return cache.get(key, default)
    except Exception:
        logger.exception("Cache backend error on get(%s)", key)
        return default


def _safe_cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        logger.exception("Cache backend error on set(%s)", key)


def _safe_cache_delete(key):
    try:
        cache.delete(key)
    except Exception:
        logger.exception("Cache backend error on delete(%s)", key)

def issue_tokens(user):
    """
    Issue a token pair and record the refresh token JTI in the cache
    (Redis in production, LocMemCache in development) as a whitelist.
    """
    refresh = RefreshToken.for_user(user)
    refresh.set_exp(lifetime=REFRESH_TOKEN_LIFETIME)

    jti = refresh['jti']
    # simplejwt's RefreshToken.for_user() always stores the user_id claim as
    # str(user.id) (see rest_framework_simplejwt.tokens.RefreshToken.for_user),
    # regardless of the field's real type. RefreshTokenView later reads that
    # claim back with `token['user_id']` and compares it against whatever we
    # cache here — store the same string form, or every single refresh
    # mismatches `int != str` and gets treated as token reuse/theft. That
    # mismatch was the actual bug behind users being logged out constantly.
    user_id = str(user.id)

    # jwt:rt:{jti} = user_id, expiring with the token
    cache_key = f"jwt:rt:{jti}"
    timeout = int(REFRESH_TOKEN_LIFETIME.total_seconds())
    _safe_cache_set(cache_key, user_id, timeout)

    # Maintain the per-user JTI collection (a list, since LocMemCache has no set type)
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = _safe_cache_get(user_set_key, [])
    if jti not in current_tokens:
        current_tokens.append(jti)
    _safe_cache_set(user_set_key, current_tokens, timeout)

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }

def _rotation_record(jti, user_id):
    """The rotation record for this jti, or None if there isn't one for this
    user. A dict value means "already rotated"; a plain string is a live,
    un-rotated whitelist entry."""
    stored = _safe_cache_get(f"jwt:rt:{jti}")
    if isinstance(stored, dict) and stored.get('user_id') == user_id:
        return stored
    return None


def get_grace_tokens(jti, user_id):
    """
    If the old JTI was rotated within REFRESH_ROTATION_GRACE, return the token
    pair it was rotated into — the concurrent-tab case this exists for.

    Returns None once the window has passed, even though the record is still
    there: see was_rotated, which the caller uses to answer 401 rather than
    letting a stale replay fall through to verify_and_revoke_refresh_token.
    """
    record = _rotation_record(jti, user_id)
    if record is None:
        return None
    rotated_at = record.get('rotated_at')
    if not isinstance(rotated_at, (int, float)):
        # Written before rotated_at was recorded. Honour it once, as the old
        # code did, rather than logging out every session mid-deploy.
        return record.get('tokens')
    if time.time() - rotated_at > REFRESH_ROTATION_GRACE.total_seconds():
        return None
    return record.get('tokens')


def was_rotated(jti, user_id):
    """True when this jti has been rotated at all, whenever that was.

    Lets the caller tell a stale replay ("rotated, but too long ago to hand
    the pair back") from an unknown jti, which matters because
    verify_and_revoke_refresh_token would read the rotation record's dict as a
    user_id mismatch and revoke every session the user has.
    """
    return _rotation_record(jti, user_id) is not None


def store_grace_tokens(jti, user_id, tokens):
    """
    After rotation, keep the new token pair under the old JTI so a later
    reuse of that same old refresh token — whether two tabs refreshing
    concurrently (the original, near-term motivation for this) or a stale
    session replaying it much later — resolves as "already rotated, here's
    what it became" rather than "never heard of this token." The latter is
    indistinguishable from an operational cache miss (a restarted dev server
    wiping LocMemCache, a Redis eviction) and used to make
    verify_and_revoke_refresh_token treat routine cache hiccups as proof of
    theft, revoking every session for the user. Kept for the token's full
    natural lifetime, not just a short race window, so that ambiguity never
    comes back once a token has been legitimately rotated at least once —
    but only handed back within REFRESH_ROTATION_GRACE (see
    get_grace_tokens); a later replay is recognized here and refused.
    """
    timeout = int(REFRESH_TOKEN_LIFETIME.total_seconds())
    _safe_cache_set(
        f"jwt:rt:{jti}",
        {'user_id': user_id, 'tokens': tokens, 'rotated_at': time.time()},
        timeout,
    )


def verify_and_revoke_refresh_token(jti, user_id):
    """
    Check that the refresh token is whitelisted; if so, revoke it so a new
    pair can be issued.

    Returns False both when the token is genuinely unknown/expired AND when
    the cache backend simply has no record of it right now (evicted, or —
    locally — wiped by a dev-server autoreload restarting LocMemCache). That
    ambiguity is deliberate: only a *mismatched* user_id on a record that
    does exist is unambiguous evidence of a forged/reused token, and only
    that case escalates to revoking every other session. A bare "not found"
    just fails this one refresh (caller logs in again normally) — treating
    it as proof of theft was the actual bug behind users getting logged out
    everywhere from routine cache hiccups.
    """
    cache_key = f"jwt:rt:{jti}"
    stored_user_id = _safe_cache_get(cache_key)

    if stored_user_id is None:
        return False

    if stored_user_id != user_id:
        # The record exists but claims a different owner — not something a
        # cache miss/eviction/restart can produce, so it's worth treating as
        # a real forgery/reuse signal.
        logger.warning("Refresh token jti=%s user mismatch (claimed=%s, stored=%s)", jti, user_id, stored_user_id)
        revoke_all_tokens_for_user(stored_user_id)
        return False

    # Revoke the old token
    _safe_cache_delete(cache_key)

    # Remove the JTI from the user's token collection. Re-extend the set's
    # own TTL to the full refresh-token lifetime on every write — omitting
    # `timeout` here would fall back to Django's cache default (300s),
    # silently expiring this tracking key long before the individual
    # `jwt:rt:{jti}` entries it's meant to enumerate, which would make
    # revoke_all_tokens_for_user() (log out all devices / password reset)
    # see an empty list and revoke nothing.
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = _safe_cache_get(user_set_key, [])
    if jti in current_tokens:
        current_tokens.remove(jti)
        _safe_cache_set(user_set_key, current_tokens, int(REFRESH_TOKEN_LIFETIME.total_seconds()))

    return True

def revoke_all_tokens_for_user(user_id):
    """
    Revoke every refresh token for the user across all devices.
    """
    user_set_key = f"jwt:user:{user_id}"
    current_tokens = _safe_cache_get(user_set_key, [])
    for jti in current_tokens:
        _safe_cache_delete(f"jwt:rt:{jti}")
    _safe_cache_delete(user_set_key)

def email_already_used(email, excluding_user_id):
    """
    Check if the email is already in use by another user's email or edu_email field.
    """
    User = get_user_model()
    from accounts.models import RegionVerification
    return User.objects.filter(email=email).exclude(id=excluding_user_id).exists() \
           or RegionVerification.objects.filter(edu_email=email, is_active=True).exclude(user_id=excluding_user_id).exists()

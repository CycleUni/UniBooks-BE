"""Where the refresh-token whitelist lives (ADR 0001).

accounts.services owns the semantics — which failures are a 503, which
degrade, when a mismatch counts as theft. A TokenStore only stores; any
backend error propagates for the service layer to classify.

The whitelist lives in Postgres (PostgresTokenStore). It used to live in the
cache as `jwt:rt:{jti}` / `jwt:user:{id}`; while
settings.AUTH_LEGACY_CACHE_FALLBACK is on, MigratingTokenStore still reads
those entries for tokens issued before the switch, copies them across on
first sight, and revokes them on logout. Nothing is written to the cache.
Turn the fallback off after one refresh-token lifetime (14 days), then
delete LegacyCacheTokenStore and MigratingTokenStore.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenRecord:
    """What the whitelist knows about one jti.

    `rotated` records outlive the rotation so a replay is recognisable as
    "already exchanged" rather than "never issued" (see
    accounts.services.store_grace_tokens). `rotated_at` is a Unix timestamp;
    None on a rotated record means it predates rotated_at being recorded.
    """
    user_id: str
    rotated: bool = False
    tokens: dict | None = None
    rotated_at: float | None = None


class TokenStore:
    def record(self, jti, user_id, ttl):
        """Whitelist a freshly issued refresh token."""
        raise NotImplementedError

    def lookup(self, jti):
        """TokenRecord for this jti, or None if unknown or expired."""
        raise NotImplementedError

    def mark_rotated(self, jti, user_id, tokens, rotated_at, ttl):
        """Replace the live entry with a rotation record."""
        raise NotImplementedError

    def forget(self, jti, user_id, ttl):
        """Stop counting a rotated jti among the user's live sessions."""
        raise NotImplementedError

    def revoke(self, jti, user_id, ttl):
        """Drop one live token."""
        raise NotImplementedError

    def revoke_all(self, user_id):
        """Drop every live token the user has."""
        raise NotImplementedError

    def purge(self, grace):
        """Housekeeping for the cleanup cron. Returns a count of rows touched."""
        return 0


class LegacyCacheTokenStore:
    """Read and revoke access to the pre-ADR-0001 cache entries. Never
    records anything: new tokens go to Postgres only."""

    @staticmethod
    def _rt_key(jti):
        return f"jwt:rt:{jti}"

    @staticmethod
    def _user_key(user_id):
        return f"jwt:user:{user_id}"

    def lookup(self, jti):
        stored = cache.get(self._rt_key(jti))
        if stored is None:
            return None
        if isinstance(stored, dict):
            return TokenRecord(
                user_id=stored.get("user_id"),
                rotated=True,
                tokens=stored.get("tokens"),
                rotated_at=stored.get("rotated_at"),
            )
        return TokenRecord(user_id=stored)

    def revoke(self, jti, user_id, ttl):
        cache.delete(self._rt_key(jti))

    def revoke_all(self, user_id):
        user_key = self._user_key(user_id)
        for jti in cache.get(user_key, []):
            cache.delete(self._rt_key(jti))
        cache.delete(user_key)


class PostgresTokenStore(TokenStore):
    """One RefreshTokenRecord row per jti.

    Rotated rows are kept until they expire so replays stay recognisable;
    revoke_all only removes live ones, as the cache store does by dropping
    rotated jtis from the user's set.
    """

    @staticmethod
    def _model():
        from accounts.models import RefreshTokenRecord
        return RefreshTokenRecord

    def record(self, jti, user_id, ttl):
        self._model().objects.create(
            jti=jti,
            user_id=user_id,
            expires_at=timezone.now() + timedelta(seconds=ttl),
        )

    def lookup(self, jti):
        row = (
            self._model().objects
            .filter(jti=jti, expires_at__gt=timezone.now())
            .only("user_id", "rotated_at", "rotated_tokens")
            .first()
        )
        if row is None:
            return None
        if row.rotated_at is None:
            return TokenRecord(user_id=str(row.user_id))
        return TokenRecord(
            user_id=str(row.user_id),
            rotated=True,
            tokens=row.rotated_tokens,
            rotated_at=row.rotated_at.timestamp(),
        )

    def mark_rotated(self, jti, user_id, tokens, rotated_at, ttl):
        self._model().objects.update_or_create(
            jti=jti,
            defaults={
                "user_id": user_id,
                "rotated_at": datetime.fromtimestamp(rotated_at, tz=dt_timezone.utc),
                "rotated_tokens": tokens,
                "expires_at": timezone.now() + timedelta(seconds=ttl),
            },
        )

    def forget(self, jti, user_id, ttl):
        # Nothing to do: a rotated row is excluded from revoke_all by its
        # rotated_at, not by membership in a separate per-user list.
        pass

    def revoke(self, jti, user_id, ttl):
        self._model().objects.filter(jti=jti, rotated_at__isnull=True).delete()

    def revoke_all(self, user_id):
        self._model().objects.filter(user_id=user_id, rotated_at__isnull=True).delete()

    def purge(self, grace):
        """Delete expired rows, and blank the token pair on rotation records
        past the grace window: it is never handed back after that, and there
        is no reason to keep usable bearer tokens in the database."""
        model = self._model()
        now = timezone.now()
        deleted, _ = model.objects.filter(expires_at__lte=now).delete()
        blanked = (
            model.objects
            .filter(rotated_at__lte=now - grace, rotated_tokens__isnull=False)
            .update(rotated_tokens=None)
        )
        return deleted + blanked


class MigratingTokenStore(TokenStore):
    """Postgres as the store of record, the legacy cache entries as a
    fallback for tokens issued before the switch.

    Legacy reads propagate errors: a Redis timeout must stay a 503, not turn
    into a 401 that signs the visitor out. Legacy revocations are best
    effort, since a failed one only leaves an entry lookup no longer reaches
    once the token has been copied across.
    """

    def __init__(self, primary=None, legacy=None):
        self.primary = primary or PostgresTokenStore()
        self.legacy = legacy or LegacyCacheTokenStore()

    def record(self, jti, user_id, ttl):
        self.primary.record(jti, user_id, ttl)

    def lookup(self, jti):
        found = self.primary.lookup(jti)
        if found is not None:
            return found
        found = self.legacy.lookup(jti)
        if found is None:
            return None
        self._adopt(jti, found)
        return found

    def _adopt(self, jti, found):
        # The cache does not expose the remaining TTL, so the copy gets a full
        # lifetime. Harmless: simplejwt still enforces the token's own exp.
        from accounts.services import REFRESH_TOKEN_LIFETIME
        ttl = int(REFRESH_TOKEN_LIFETIME.total_seconds())
        if found.rotated:
            self.primary.mark_rotated(jti, found.user_id, found.tokens, found.rotated_at or 0, ttl)
        else:
            self.primary.record(jti, found.user_id, ttl)

    def mark_rotated(self, jti, user_id, tokens, rotated_at, ttl):
        self.primary.mark_rotated(jti, user_id, tokens, rotated_at, ttl)

    def forget(self, jti, user_id, ttl):
        self.primary.forget(jti, user_id, ttl)

    def revoke(self, jti, user_id, ttl):
        self.primary.revoke(jti, user_id, ttl)
        self._legacy_best_effort("revoke", jti, user_id, ttl)

    def revoke_all(self, user_id):
        self.primary.revoke_all(user_id)
        self._legacy_best_effort("revoke_all", user_id)

    def purge(self, grace):
        return self.primary.purge(grace)

    def _legacy_best_effort(self, method, *args):
        try:
            getattr(self.legacy, method)(*args)
        except Exception:
            logger.exception("Legacy token store error on %s", method)


def get_token_store():
    """Resolved per call so a test's override_settings takes effect; the
    stores are stateless."""
    if getattr(settings, "AUTH_LEGACY_CACHE_FALLBACK", False):
        return MigratingTokenStore()
    return PostgresTokenStore()

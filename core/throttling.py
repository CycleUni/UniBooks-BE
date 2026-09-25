"""Where DRF rate-limit counters live (ADR 0001).

ScopedThrottle is a drop-in for DRF's ScopedRateThrottle — same scopes, same
DEFAULT_THROTTLE_RATES, same keys — that counts in a ThrottleStore instead of
CACHES["default"]. DRF's own throttle reads a history list from the cache,
appends to it and writes it back, so concurrent requests overwrite each
other's entries and a burst of login attempts counts as fewer than it was.
Here each hit is one atomic upsert in Postgres.

The ThrottleStore interface lets another store (a Redis INCR, say) be
swapped in without touching views.
"""

import time
from datetime import timedelta

from django.db import connection
from rest_framework.throttling import ScopedRateThrottle


def _now():
    # A seam for tests to pin the window without patching the time module
    # for everything else (JWT expiry, token grace windows).
    return time.time()


class ThrottleStore:
    def hit(self, key, limit, window):
        """Count one request against `key`.

        Returns (allowed, retry_after_seconds). A refused request still
        counts, so hammering a closed window keeps it closed.
        """
        raise NotImplementedError

    def purge(self):
        """Housekeeping for the cleanup cron. Returns a count of rows removed."""
        return 0


class PostgresThrottleStore(ThrottleStore):
    """Fixed windows aligned to the epoch: a burst straddling a boundary can
    reach twice the limit, the usual price of a counter that needs one
    upsert per request instead of a history list."""

    # Rows older than this can no longer belong to any live window: the
    # longest configured rate period is an hour.
    RETENTION = timedelta(days=1)

    def hit(self, key, limit, window):
        now = _now()
        window_start = int(now // window) * window
        from core.models import ThrottleCounter

        q = connection.ops.quote_name
        table, k, w, c = q(ThrottleCounter._meta.db_table), q("key"), q("window_start"), q("count")
        # ON CONFLICT ... RETURNING: one atomic statement, so concurrent
        # requests cannot both read the same count. Supported by Postgres and
        # by SQLite >= 3.35, which the test suite runs on.
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} ({k}, {w}, {c}) VALUES (%s, %s, 1) "
                f"ON CONFLICT ({k}, {w}) DO UPDATE SET {c} = {table}.{c} + 1 "
                f"RETURNING {c}",
                [key, window_start],
            )
            (count,) = cursor.fetchone()
        return count <= limit, window_start + window - now

    def purge(self):
        from core.models import ThrottleCounter

        cutoff = _now() - self.RETENTION.total_seconds()
        deleted, _ = ThrottleCounter.objects.filter(window_start__lt=cutoff).delete()
        return deleted


def get_throttle_store():
    return PostgresThrottleStore()


def purge_throttle_counters():
    return get_throttle_store().purge()


class ScopedThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        # ScopedRateThrottle's own setup, then the store in place of
        # SimpleRateThrottle's cache history.
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        if self.rate is None:
            return True
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True

        allowed, self._retry_after = get_throttle_store().hit(self.key, self.num_requests, self.duration)
        return allowed

    def wait(self):
        return max(getattr(self, "_retry_after", 0), 0)

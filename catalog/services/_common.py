import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Sentinel value used to mark a cached "confirmed not found" result. This is
# stored as JSON, so it must be distinguishable from `json.dumps(None)` /
# `json.dumps([])`, which would otherwise look like a plain cache miss once
# decoded (both are falsy in Python).
_NOT_FOUND_SENTINEL = "__gb_not_found__"

# Negative-result cache TTL: shorter than the positive-hit TTLs so a book
# later added to Google's catalog is eventually rediscovered, while repeated
# no-op queries in the short term don't re-hit the API. This is distinct from
# _GOOGLE_RATE_LIMIT_TTL below — one caches "we confirmed this book doesn't
# exist", the other caches "Google is rate-limiting us right now" — very
# different facts with very different appropriate lifetimes.
_NOT_FOUND_CACHE_TTL = 3600  # 1 hour


def _safe_cache_get(key):
    """cache.get() that degrades to a plain cache miss on a Redis hiccup
    (timeout, connection reset) instead of crashing the whole request — this
    cache is purely a performance optimization over Google/Open Library, not
    a correctness requirement, so callers should keep working live when it's
    unavailable rather than surfacing a 500."""
    try:
        return cache.get(key)
    except Exception:
        logger.exception("Cache backend error on get(%s); treating as a miss", key)
        return None


def _safe_cache_set(key, value, timeout):
    """cache.set() that swallows a Redis hiccup instead of crashing the
    request — see `_safe_cache_get`. Losing this write just means the next
    lookup re-fetches live, which is the same outcome as a cold cache."""
    try:
        cache.set(key, value, timeout=timeout)
    except Exception:
        logger.exception("Cache backend error on set(%s); continuing without caching", key)


def describe_source(engine, cache_hit):
    """Human-readable provenance label for developer monitoring only (not
    shown in the UI, not the persisted Book.source enum) — distinguishes a
    live external-API call from a cache hit, per engine."""
    label = 'google books' if engine == 'googlebooks' else 'Open Library API'
    return f"{label} cache" if cache_hit else label

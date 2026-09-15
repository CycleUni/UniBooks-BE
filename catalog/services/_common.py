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

# Per-call timeout for the external catalogue APIs. With Vercel maxDuration
# increased to 30s, 5s allows cold-start connections and upstream crawling
# (ISBNnet, Google Books) to reliably complete without timing out.
EXTERNAL_API_TIMEOUT = 5

# Outcome status for external book catalogue lookups
STATUS_FOUND = "found"
STATUS_NOT_FOUND = "not_found"
STATUS_TIMEOUT = "timeout"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_ERROR = "error"


def _set_status(meta, value):
    """Set the lookup status on a meta dict when one was provided."""
    if meta is not None:
        meta['status'] = value


# Pairs of characters a catalogue has been seen to wrap a whole field in.
_WRAPPING_QUOTES = (('"', '"'), ('\u201c', '\u201d'))


def clean_publisher(value):
    """A publisher name without the quotation marks some records wrap it in.

    Google Books returns `"O'Reilly Media, Inc."` — quotes included, as part
    of the string — for O'Reilly titles, and the book page printed them
    verbatim. Only a pair that wraps the whole value is removed, and only
    when that quote character does not also appear inside it: `"A" & "B"`
    is two quoted names, not one wrapped one, and stays as it is.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    for opening, closing in _WRAPPING_QUOTES:
        inner = text[len(opening):-len(closing)]
        if (
            len(text) > len(opening) + len(closing)
            and text.startswith(opening)
            and text.endswith(closing)
            and opening not in inner
            and closing not in inner
        ):
            return inner.strip()
    return text


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
    if engine == 'googlebooks':
        label = 'google books'
    elif engine == 'isbnnet':
        label = 'ISBNnet'
    else:
        label = 'Open Library API'
    return f"{label} cache" if cache_hit else label

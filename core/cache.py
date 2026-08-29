"""Cache invalidation.

One rule everywhere: **a write invalidates the caches it dirties**, in every
environment. Only the mechanism differs, and only because of key shape:

1. `bump_cache_version()` — for key families whose names can't be enumerated
   (`listing_list`, `listing:<pk>`, `book_detail`). Their keys embed page,
   school, language and limit, so there is no finite set of names to delete.
   Bumping a generation retires the whole family in one O(1) write.

2. `safe_cache_delete()` — for single, known keys (`home_static_*`,
   `home_waitlist`). Nothing clever needed; it only swallows backend errors,
   since these run inside model signals and request handlers where a Redis
   hiccup must not take the surrounding write down with it.

The one exception is `[REGION]_recent_books_*`, which has no invalidation hook and
relies purely on its (short) TTL.

There is deliberately no dev/production switch. An earlier version gated the
`home_waitlist` invalidation on a setting, on the theory that clearing a
shared homepage aggregate on every subscribe would stampede the database.
Measured against this project's actual write volume that risk was imaginary,
and the divergence cost more than it saved: two behaviours to reason about, a
config knob nobody would ever set, and dev/prod acting differently — the kind
of gap that produces bugs which only reproduce in production. If write volume
ever genuinely makes an invalidation expensive, gate that one key then, with
real numbers.
"""

import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# TTLs
#
# Two different meanings, so they're kept apart deliberately:
#
#   *_TTL below on version-stamped keys is only a memory bound. Correctness
#   comes from the version bump on write, so staleness is ~0 regardless of TTL.
#
#   HOME_RECENT_TTL is the staleness window in the literal sense:
#   `recent_books_*` has no invalidation hook, so this number is exactly how
#   long a newly listed book can be missing from the homepage. Short because
#   the dataset is currently small — there is little query cost to save.
#   Raise it when data volume actually makes the rebuild expensive.
# ---------------------------------------------------------------------
LISTING_CACHE_TTL = 300
BOOK_DETAIL_CACHE_TTL = 300
HOME_RECENT_TTL = 60
# Both invalidated on write, so these TTLs are backstops, not staleness windows.
HOME_WAITLIST_TTL = 300
HOME_STATIC_TTL = 86400


# ---------------------------------------------------------------------
# Generation counters ("version stamping")
#
# Solves the problem that most content keys embed unbounded values — page
# number, school, language, limit — so there is no finite set of keys to
# delete when the underlying data changes. Django's cache API has no
# delete-by-pattern, and `cache.clear()` is not an option here: the same
# cache holds the JWT refresh-token whitelist, so clearing it would log
# every user out.
#
# Instead each family of keys carries a generation number. Bumping the
# generation makes every key in that family unreachable in one O(1) write;
# the orphaned entries simply expire on their own TTL.
# ---------------------------------------------------------------------


def cache_version(namespace):
    """Current generation for `namespace`."""
    key = f"cachever:{namespace}"
    try:
        version = cache.get(key)
        if version is not None:
            return version
        # Seed from the wall clock rather than from 0. If this counter is
        # ever evicted, restarting low could re-issue a generation that
        # older-but-still-unexpired entries were written under, resurrecting
        # stale data. Seconds-since-epoch only ever moves forward.
        seed = int(time.time())
        cache.add(key, seed, timeout=None)
        return cache.get(key) or seed
    except Exception:
        logger.exception("Cache backend error reading generation for %s", namespace)
        return 0


def bump_cache_version(namespace):
    """Invalidate every key in `namespace` at once."""
    key = f"cachever:{namespace}"
    try:
        cache.incr(key)
    except ValueError:
        # Counter absent: the next reader seeds it from the clock, which is
        # already newer than any generation previously handed out.
        try:
            cache.set(key, int(time.time()), timeout=None)
        except Exception:
            logger.exception("Cache backend error seeding generation for %s", namespace)
    except Exception:
        logger.exception("Cache backend error bumping generation for %s", namespace)


def region_versioned_key(region, namespace, *parts):
    """Cache key inside `namespace`'s current generation, specific to a region."""
    # Prefix with region code to ensure strict hard-isolation of caches per region.
    suffix = "_".join(str(part) for part in parts)
    return f"{region.code}_{namespace}_v{cache_version(namespace)}_{suffix}"

def versioned_key(namespace, *parts):
    """Cache key inside `namespace`'s current generation."""
    suffix = "_".join(str(part) for part in parts)
    return f"{namespace}_v{cache_version(namespace)}_{suffix}"


def safe_cache_delete(*keys):
    """Drop known cache keys, tolerating a cache-backend outage.

    Callers run inside model signals and request handlers, so a Redis hiccup
    must not take the surrounding write down with it — a stale cache entry is
    far cheaper than a failed subscribe or a 500 on an admin edit.
    """
    for key in keys:
        try:
            cache.delete(key)
        except Exception:
            logger.exception("Cache backend error invalidating %s", key)

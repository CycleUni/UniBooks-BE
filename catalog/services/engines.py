"""Which external catalogues a book lookup may use, and in what order.

Shared by the search endpoint and the book detail endpoint, so that both
answer to the region's `search_engines` setting in the same way. The lookup
functions themselves are passed in by the caller rather than imported here:
the views' tests patch them on the view module (`search.views.…`,
`catalog.views.…`), and a reference taken here would slip past those patches.
"""

from .google_books import GoogleBooksRateLimited

VALID_SEARCH_ENGINES = frozenset({'googlebooks', 'openlibrary', 'isbnnet'})

# The order tried when the caller did not name an engine. Every step runs
# when the one before it has no record of the book, not only when it fails:
# Google's catalogue is thin on Taiwanese textbooks, which is what the ISBN
# registry is for. The registry answers exact ISBN lookups only, so a keyword
# search skips it.
ISBN_FALLBACK_ORDER = ('googlebooks', 'isbnnet', 'openlibrary')
KEYWORD_FALLBACK_ORDER = ('googlebooks', 'openlibrary')

SOURCE_BY_ENGINE = {
    'googlebooks': 'google_api',
    'openlibrary': 'openlibrary_api',
    'isbnnet': 'isbnnet_api',
}


def region_search_engines(region):
    """The engines the region's admin settings allow. A region with none
    configured keeps the long-standing Google-only default."""
    configured = getattr(region, 'search_engines', None) or []
    allowed = [engine for engine in configured if engine in VALID_SEARCH_ENGINES]
    return allowed or ['googlebooks']


def engine_order(requested, allowed, fallback_order):
    """A named engine the region allows is used on its own; anything else —
    no engine, an unknown one, or one this region has switched off — gets
    the region's share of the fallback order."""
    if requested in allowed:
        return [requested]
    return [engine for engine in fallback_order if engine in allowed]


def lookup_in_order(query, order, lookups, allowed):
    """Ask each engine in `order` until one returns something.

    Returns (result, engine_used, meta, google_unavailable). `lookups` maps
    an engine to a `fn(query, _meta=dict)`. A Google rate limit also hands
    over to Open Library when the region allows it, even if the caller named
    Google: that is a failure to answer, not an answer of "no such book".
    """
    order = list(order)
    result, engine_used, meta = None, None, {}
    google_unavailable = False
    attempts = []
    i = 0
    while i < len(order):
        engine_used = order[i]
        i += 1
        meta = {}
        try:
            result = lookups[engine_used](query, _meta=meta)
        except GoogleBooksRateLimited:
            google_unavailable = True
            result = None
            meta['status'] = 'rate_limited'
            if 'openlibrary' in allowed and 'openlibrary' not in order:
                order.append('openlibrary')
        attempts.append({
            'engine': engine_used,
            'status': meta.get('status', 'not_found' if not result else 'found'),
            'cache_hit': meta.get('cache_hit', False),
        })
        if result:
            break
    meta['attempts'] = attempts
    return result, engine_used, meta, google_unavailable

import json
import logging

import requests
from django.conf import settings

from ._common import (
    _NOT_FOUND_CACHE_TTL,
    _NOT_FOUND_SENTINEL,
    _safe_cache_get,
    _safe_cache_set,
)

logger = logging.getLogger(__name__)


def get_isbnnet_book_by_isbn(isbn, _meta=None):
    """Lookup book metadata by exact ISBN via the ISBNnet Resolver microservice
    (Cloudflare Worker querying National Central Library data)."""
    cache_key = f"isbnnet:isbn:{isbn}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for ISBNnet ISBN: %s", isbn)
            return None
        logger.debug("Cache hit for ISBNnet ISBN: %s", isbn)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    logger.debug("ISBNnet ISBN lookup: isbn=%s", isbn)

    try:
        url = f"{settings.ISBNNET_RESOLVER_URL.rstrip('/')}/isbn/{isbn}"
        response = requests.get(
            url,
            headers={'User-Agent': 'CycleUni Backend Service'},
            timeout=5,
        )
        logger.debug("ISBNnet ISBN lookup response status=%s", response.status_code)
        if response.status_code == 200:
            data = response.json()
            result = {
                'title': data.get('title', ''),
                'authors': data.get('authors', ''),
                'publisher': data.get('publisher', ''),
                'published_date': data.get('published_date', ''),
                'cover_url': data.get('cover_url', ''),
                'isbn': data.get('isbn', isbn),
            }
            _safe_cache_set(cache_key, json.dumps(result), 86400 * 30)
            return result
        elif response.status_code == 404:
            _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
            return None
        else:
            logger.warning("ISBNnet lookup returned unexpected status %s for %s", response.status_code, isbn)
            return None
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("ISBNnet ISBN lookup failed for %s", isbn)
    return None

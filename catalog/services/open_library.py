import hashlib
import json
import logging

import requests

from ._common import (
    EXTERNAL_API_TIMEOUT,
    _NOT_FOUND_CACHE_TTL,
    _NOT_FOUND_SENTINEL,
    _safe_cache_get,
    _safe_cache_set,
)

logger = logging.getLogger(__name__)

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPEN_LIBRARY_ISBN_URL = "https://openlibrary.org/api/books"
OPEN_LIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"


def get_open_library_book_by_isbn(isbn, _meta=None):
    """Open Library equivalent of `get_google_books_by_isbn`, used as the
    automatic fallback when Google Books rate-limits us (429), or when the
    caller explicitly picks Open Library as the search engine."""
    cache_key = f"ol:isbn:{isbn}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for Open Library ISBN: %s", isbn)
            return None
        logger.debug("Cache hit for Open Library ISBN: %s", isbn)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    logger.debug("Open Library ISBN lookup: isbn=%s", isbn)

    try:
        response = requests.get(
            OPEN_LIBRARY_ISBN_URL,
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=EXTERNAL_API_TIMEOUT,
        )
        logger.debug("Open Library ISBN lookup response status=%s", response.status_code)
        if response.status_code == 200:
            data = response.json()
            entry = data.get(f"ISBN:{isbn}")
            if not entry:
                _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
                return None
            result = {
                'title': entry.get('title', ''),
                'authors': ', '.join(a.get('name', '') for a in entry.get('authors', [])),
                'publisher': ', '.join(p.get('name', '') for p in entry.get('publishers', [])),
                'published_date': entry.get('publish_date', ''),
                'cover_url': entry.get('cover', {}).get('medium', ''),
                'isbn': isbn,
            }
            _safe_cache_set(cache_key, json.dumps(result), 86400 * 30)
            return result
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Open Library ISBN lookup failed for %s", isbn)
    return None


def search_open_library_books(query, _meta=None):
    """Open Library equivalent of `search_google_books`."""
    query_hash = hashlib.sha1(query.encode('utf-8')).hexdigest()
    cache_key = f"ol:q:{query_hash}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for Open Library query: %r", query)
            return []
        logger.debug("Cache hit for Open Library query: %r", query)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    logger.debug("Open Library search: query=%r", query)

    try:
        response = requests.get(
            OPEN_LIBRARY_SEARCH_URL,
            # Open Library's search.json omits `isbn` by default; without it,
            # every doc below would have isbn=None and get silently dropped
            # by the dedup step in search/views.py (it only keeps items with
            # a truthy isbn), so search results would never actually appear.
            params={"q": query, "limit": 10, "fields": "title,author_name,publisher,first_publish_year,cover_i,isbn"},
            timeout=EXTERNAL_API_TIMEOUT,
        )
        logger.debug("Open Library search response status=%s", response.status_code)
        if response.status_code == 200:
            data = response.json()
            docs = data.get('docs', [])[:10]
            if not docs:
                _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
                return []
            results = []
            for doc in docs:
                cover_id = doc.get('cover_i')
                isbns = doc.get('isbn') or []
                # A work can list dozens of ISBNs across editions/formats,
                # mixing ISBN-10 and ISBN-13; prefer an ISBN-13 (matches what
                # Google Books gives us and what the rest of the app expects),
                # falling back to whatever's first if none is 13 digits.
                isbn13 = next((i for i in isbns if len(i) == 13), None) or (isbns[0] if isbns else None)
                results.append({
                    'title': doc.get('title', ''),
                    'authors': ', '.join(doc.get('author_name', [])),
                    'publisher': (doc.get('publisher') or [''])[0],
                    'published_date': str(doc.get('first_publish_year', '')),
                    'cover_url': OPEN_LIBRARY_COVER_URL.format(cover_id=cover_id) if cover_id else '',
                    'isbn': isbn13,
                })
            # cache for 24 hours, matching search_google_books
            _safe_cache_set(cache_key, json.dumps(results), 86400)
            return results
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Open Library search failed for query %r", query)
    return []

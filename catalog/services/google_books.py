import hashlib
import json
import logging

import requests
from django.conf import settings

from ._common import (
    EXTERNAL_API_TIMEOUT,
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
    STATUS_TIMEOUT,
    _NOT_FOUND_CACHE_TTL,
    _NOT_FOUND_SENTINEL,
    _safe_cache_get,
    _safe_cache_set,
    _set_status,
    clean_publisher,
)
from .isbn import isbn_forms

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooksRateLimited(Exception):
    """Raised when Google Books responds 429. Callers (search/catalog views)
    catch this to automatically fall back to the Open Library API instead of
    surfacing an error to the user."""


# When Google Books responds 429, remember it for a short window so the next
# request (for any ISBN/query) skips straight to the fallback instead of
# immediately re-hitting Google and getting rate-limited again — 429s are a
# property of the whole API key/quota, not of any one query, so this is a
# single global flag rather than per-query. Short on purpose: a rate limit
# is usually transient, so we want to start trying Google again soon rather
# than staying on the fallback for as long as a real "not found" result.
_GOOGLE_RATE_LIMIT_CACHE_KEY = "gb:rate_limited"
_GOOGLE_RATE_LIMIT_TTL = 10  # seconds


def _google_books_params(query):
    params = {'q': query}
    api_key = settings.GOOGLE_BOOKS_API_KEY
    if api_key and not api_key.startswith('dev-only'):
        params['key'] = api_key
    return params


def get_google_books_by_isbn(isbn, _meta=None):
    cache_key = f"gb:isbn:{isbn}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for ISBN: %s", isbn)
            _set_status(_meta, STATUS_NOT_FOUND)
            return None
        logger.debug("Cache hit for ISBN: %s", isbn)
        _set_status(_meta, STATUS_FOUND)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    if _safe_cache_get(_GOOGLE_RATE_LIMIT_CACHE_KEY):
        logger.debug("Google Books known rate-limited (cached); skipping live call for isbn=%s", isbn)
        _set_status(_meta, STATUS_RATE_LIMITED)
        raise GoogleBooksRateLimited(f"cached rate-limit, isbn={isbn}")

    params = _google_books_params(f"isbn:{isbn}")
    debug_params = params.copy()
    if 'key' in debug_params:
        debug_params['key'] = '***'
    logger.debug("Google Books ISBN lookup: isbn=%s params=%s", isbn, debug_params)

    try:
        response = requests.get(
            GOOGLE_BOOKS_API_URL,
            params=params,
            headers={'User-Agent': 'UniBooks Backend Service'},
            timeout=EXTERNAL_API_TIMEOUT,
        )
        logger.debug("Google Books ISBN lookup response status=%s", response.status_code)
        if response.status_code == 429:
            logger.warning("Google Books rate-limited (429) for ISBN %s", isbn)
            _safe_cache_set(_GOOGLE_RATE_LIMIT_CACHE_KEY, True, _GOOGLE_RATE_LIMIT_TTL)
            _set_status(_meta, STATUS_RATE_LIMITED)
            raise GoogleBooksRateLimited(f"429 for isbn={isbn}")
        if response.status_code != 200:
            logger.debug("Google Books ISBN lookup response body: %s", response.text)
            _set_status(_meta, STATUS_ERROR)
            return None
        data = response.json()
        item = data['items'][0]['volumeInfo'] if data.get('totalItems', 0) > 0 and data.get('items') else None
        if item is None:
            # Google's isbn: operator has been answering zero for books it
            # holds (seen 2026-10 for every ISBN tried). A plain search for
            # the number still finds some of them among unrelated results.
            item = _volume_with_isbn(isbn)
        if item is not None:
            result = {
                'title': item.get('title', ''),
                'authors': ', '.join(item.get('authors', [])),
                'publisher': clean_publisher(item.get('publisher', '')),
                'published_date': item.get('publishedDate', ''),
                'cover_url': item.get('imageLinks', {}).get('thumbnail', '').replace('http:', 'https:'),
                'isbn': isbn,
            }
            # cache for 30 days
            _safe_cache_set(cache_key, json.dumps(result), 86400 * 30)
            _set_status(_meta, STATUS_FOUND)
            return result
        # Confirmed zero results: negatively cache for a shorter TTL.
        _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
        _set_status(_meta, STATUS_NOT_FOUND)
        return None
    except requests.exceptions.Timeout:
        logger.warning("Google Books ISBN lookup timed out for %s", isbn)
        _set_status(_meta, STATUS_TIMEOUT)
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Google Books ISBN lookup failed for %s", isbn)
        _set_status(_meta, STATUS_ERROR)
    return None


def _volume_with_isbn(isbn):
    """The volume a plain search for `isbn` returns that carries that exact
    ISBN, or None. Only an identifier match counts: the rest of such a
    search is whatever mentions the number. A 429 raises like the main call."""
    params = _google_books_params(isbn)
    params['maxResults'] = 40
    response = requests.get(
        GOOGLE_BOOKS_API_URL,
        params=params,
        headers={'User-Agent': 'UniBooks Backend Service'},
        timeout=EXTERNAL_API_TIMEOUT,
    )
    if response.status_code == 429:
        logger.warning("Google Books rate-limited (429) on plain search for ISBN %s", isbn)
        _safe_cache_set(_GOOGLE_RATE_LIMIT_CACHE_KEY, True, _GOOGLE_RATE_LIMIT_TTL)
        raise GoogleBooksRateLimited(f"429 for plain isbn={isbn}")
    if response.status_code != 200:
        return None
    wanted = isbn_forms(isbn)
    for volume in response.json().get('items') or []:
        info = volume.get('volumeInfo') or {}
        ids = {(x.get('identifier') or '').upper() for x in info.get('industryIdentifiers') or []}
        if ids & wanted:
            return info
    return None


def search_google_books(query, _meta=None):
    query_hash = hashlib.sha1(query.encode('utf-8')).hexdigest()
    cache_key = f"gb:q:{query_hash}"
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        if _meta is not None:
            _meta['cache_hit'] = True
        if cached == _NOT_FOUND_SENTINEL:
            logger.debug("Cache hit (not found) for query: %r", query)
            _set_status(_meta, STATUS_NOT_FOUND)
            return []
        logger.debug("Cache hit for query: %r", query)
        _set_status(_meta, STATUS_FOUND)
        return json.loads(cached)
    if _meta is not None:
        _meta['cache_hit'] = False

    if _safe_cache_get(_GOOGLE_RATE_LIMIT_CACHE_KEY):
        logger.debug("Google Books known rate-limited (cached); skipping live call for query=%r", query)
        _set_status(_meta, STATUS_RATE_LIMITED)
        raise GoogleBooksRateLimited(f"cached rate-limit, query={query!r}")

    params = _google_books_params(query)
    debug_params = params.copy()
    if 'key' in debug_params:
        debug_params['key'] = '***'
    logger.debug("Google Books search: query=%r params=%s", query, debug_params)

    try:
        response = requests.get(
            GOOGLE_BOOKS_API_URL,
            params=params,
            headers={'User-Agent': 'UniBooks Backend Service'},
            timeout=EXTERNAL_API_TIMEOUT,
        )
        logger.debug("Google Books search response status=%s", response.status_code)
        if response.status_code == 429:
            logger.warning("Google Books rate-limited (429) for query %r", query)
            _safe_cache_set(_GOOGLE_RATE_LIMIT_CACHE_KEY, True, _GOOGLE_RATE_LIMIT_TTL)
            _set_status(_meta, STATUS_RATE_LIMITED)
            raise GoogleBooksRateLimited(f"429 for query={query!r}")
        if response.status_code != 200:
            logger.debug("Google Books search response body: %s", response.text)
            _set_status(_meta, STATUS_ERROR)
            return []
        data = response.json()
        items = data.get('items', [])
        if not items:
            # Confirmed zero results: negatively cache for a shorter TTL.
            _safe_cache_set(cache_key, _NOT_FOUND_SENTINEL, _NOT_FOUND_CACHE_TTL)
            _set_status(_meta, STATUS_NOT_FOUND)
            return []
        results = []
        for item in items[:10]:
            info = item.get('volumeInfo', {})
            results.append({
                'title': info.get('title', ''),
                'authors': ', '.join(info.get('authors', [])),
                'publisher': clean_publisher(info.get('publisher', '')),
                'published_date': info.get('publishedDate', ''),
                'cover_url': info.get('imageLinks', {}).get('thumbnail', '').replace('http:', 'https:'),
                'isbn': next((id['identifier'] for id in info.get('industryIdentifiers', []) if id['type'] == 'ISBN_13'), None)
            })
        # cache for 24 hours
        _safe_cache_set(cache_key, json.dumps(results), 86400)
        _set_status(_meta, STATUS_FOUND)
        return results
    except requests.exceptions.Timeout:
        logger.warning("Google Books search timed out for query %r", query)
        _set_status(_meta, STATUS_TIMEOUT)
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Google Books search failed for query %r", query)
        _set_status(_meta, STATUS_ERROR)
    return []

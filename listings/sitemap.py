"""The dynamic half of the site's sitemap: book and listing pages.

The frontend ships the fixed pages (home, search, sell) in its own static
/sitemap.xml; only the pages that come and go with the data are built here.
Both files are named in the frontend's robots.txt, which is what lets search
engines accept this one although it is served from the API host while every
URL in it points at FRONTEND_URL.

Only what is for sale goes in: a book page is listed while at least one of its
region's listings is active, and a listing page while it is itself active. A
sold or removed listing drops out on the next rebuild instead of sending a
crawler to a page that has nothing left to buy.
"""

from urllib.parse import urlencode
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.db.models import Max
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from catalog.models import Book
from listings.models import Listing

# The sitemap protocol's cap on URLs per file. Books are written first, so if
# the cap is ever reached it is the (thinner) listing pages that are cut, the
# newest kept. Past that point this should become a sitemap index.
MAX_URLS = 50_000

# No write invalidates this: it is anonymous, unthrottled and scans every
# active listing, so it is rebuilt at most once per TTL, which is also the
# staleness window. Crawlers read a sitemap far less often than that.
SITEMAP_TTL = 3600
CACHE_KEY = 'sitemap_dynamic_xml'


def _frontend_origin():
    return settings.FRONTEND_URL.rstrip('/')


def _entry(loc, lastmod):
    return (
        f'<url><loc>{escape(loc)}</loc>'
        f'<lastmod>{lastmod.date().isoformat()}</lastmod></url>'
    )


def _book_entries(origin):
    books = (
        Book.objects
        .filter(listings__status='active')
        .annotate(last_listed=Max('listings__updated_at'))
        .values('id', 'isbn13', 'region_id', 'last_listed')
        .order_by('-last_listed')
    )
    for book in books.iterator():
        # The same identity the book page puts in its canonical link
        # (bookQueryParams in the frontend): the ISBN when there is one.
        query = {'isbn': book['isbn13']} if book['isbn13'] else {'id': book['id']}
        loc = f"{origin}/{book['region_id'].lower()}/book?{urlencode(query)}"
        yield _entry(loc, book['last_listed'])


def _listing_entries(origin):
    listings = (
        Listing.objects
        .filter(status='active')
        .values('id', 'region_id', 'updated_at')
        .order_by('-updated_at')
    )
    for listing in listings.iterator():
        loc = f"{origin}/{listing['region_id'].lower()}/listing/{listing['id']}"
        yield _entry(loc, listing['updated_at'])


def build_sitemap():
    origin = _frontend_origin()
    entries = []
    for source in (_book_entries(origin), _listing_entries(origin)):
        for entry in source:
            if len(entries) >= MAX_URLS:
                break
            entries.append(entry)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + ''.join(f'{e}\n' for e in entries)
        + '</urlset>\n'
    )


@require_GET
def sitemap_view(request):
    xml = cache.get(CACHE_KEY)
    if xml is None:
        xml = build_sitemap()
        cache.set(CACHE_KEY, xml, SITEMAP_TTL)
    response = HttpResponse(xml, content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = f'public, max-age={SITEMAP_TTL}'
    return response

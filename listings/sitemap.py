"""The dynamic half of the site's sitemap: book and listing pages.

The frontend ships the fixed pages (home, search, sell) in its own static
/sitemap.xml; only the pages that come and go with the data are built here.
Both files are named in the frontend's robots.txt, which is what lets search
engines accept this one although it is served from the API host while every
URL in it points at FRONTEND_URL.

/api/v1/sitemap.xml is a sitemap index. It names one sitemap per kind of page,
region and page of PAGE_SIZE URLs — /api/v1/sitemap/books-tw-1.xml,
listings-hk-2.xml and so on — so no single file grows with the catalogue, and
each carries a lastmod that lets a crawler skip the ones that have not changed.
Pages are cut in id order rather than by date, so a URL stays on the same page
when its listing is edited instead of shifting every page after it.

Only what is for sale goes in: a book page is listed while at least one of its
region's listings is active, and a listing page while it is itself active. A
sold or removed listing drops out on the next rebuild instead of sending a
crawler to a page that has nothing left to buy.
"""

from urllib.parse import urlencode
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Max
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_GET

from catalog.models import Book
from core.region import _get_active_regions
from listings.models import Listing

# Well under the protocol's 50,000 URLs / 50 MB per file: about 700 KB of XML.
PAGE_SIZE = 5000

# No write invalidates these: they are anonymous and unthrottled, so each file
# is rebuilt at most once per TTL, which is also the staleness window.
# Crawlers read a sitemap far less often than that.
SITEMAP_TTL = 3600

XMLNS = 'http://www.sitemaps.org/schemas/sitemap/0.9'


def _books(region_code):
    return (
        Book.objects
        .filter(region_id=region_code, listings__status='active')
        .annotate(lastmod=Max('listings__updated_at'))
        .order_by('id')
    )


def _listings(region_code):
    return (
        Listing.objects
        .filter(region_id=region_code, status='active')
        .annotate(lastmod=F('updated_at'))
        .order_by('id')
    )


def _book_loc(origin, region, row):
    # The same identity the book page puts in its canonical link
    # (bookQueryParams in the frontend): the ISBN when there is one.
    query = {'isbn': row['isbn13']} if row['isbn13'] else {'id': row['id']}
    return f"{origin}/{region}/book?{urlencode(query)}"


def _listing_loc(origin, region, row):
    return f"{origin}/{region}/listing/{row['id']}"


KINDS = {
    'books': (_books, ('id', 'isbn13', 'lastmod'), _book_loc),
    'listings': (_listings, ('id', 'lastmod'), _listing_loc),
}


def _region_codes():
    return list(_get_active_regions())


def _xml_response(xml):
    response = HttpResponse(xml, content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = f'public, max-age={SITEMAP_TTL}'
    return response


def _cached(key, build):
    xml = cache.get(key)
    if xml is None:
        xml = build()
        cache.set(key, xml, SITEMAP_TTL)
    return xml


def _page_lastmods(kind, region_code):
    """The newest lastmod on each page of one kind and region, in page order.

    Reads only two columns of every row, which is what the index needs to
    date each page without building the pages themselves.
    """
    queryset, _, _ = KINDS[kind]
    stamps = queryset(region_code).values_list('lastmod', flat=True)
    lastmods = []
    for i, stamp in enumerate(stamps.iterator()):
        if i % PAGE_SIZE == 0:
            lastmods.append(stamp)
        elif stamp > lastmods[-1]:
            lastmods[-1] = stamp
    return lastmods


def build_index(request):
    entries = []
    for region_code in _region_codes():
        region = region_code.lower()
        for kind in KINDS:
            for page, lastmod in enumerate(_page_lastmods(kind, region_code), start=1):
                loc = request.build_absolute_uri(
                    reverse('sitemap-section', kwargs={'kind': kind, 'region': region, 'page': page})
                )
                entries.append(
                    f'<sitemap><loc>{escape(loc)}</loc>'
                    f'<lastmod>{lastmod.date().isoformat()}</lastmod></sitemap>\n'
                )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sitemapindex xmlns="{XMLNS}">\n'
        + ''.join(entries)
        + '</sitemapindex>\n'
    )


def build_section(kind, region, page):
    queryset, fields, loc = KINDS[kind]
    origin = settings.FRONTEND_URL.rstrip('/')
    start = (page - 1) * PAGE_SIZE
    rows = queryset(region.upper()).values(*fields)[start:start + PAGE_SIZE]
    entries = [
        f'<url><loc>{escape(loc(origin, region, row))}</loc>'
        f'<lastmod>{row["lastmod"].date().isoformat()}</lastmod></url>\n'
        for row in rows
    ]
    if not entries:
        return None
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{XMLNS}">\n'
        + ''.join(entries)
        + '</urlset>\n'
    )


@require_GET
@gzip_page
def sitemap_index_view(request):
    # Keyed by host because the child URLs are built from it.
    xml = _cached(f'sitemap_index:{request.get_host()}', lambda: build_index(request))
    return _xml_response(xml)


@require_GET
@gzip_page
def sitemap_section_view(request, kind, region, page):
    page = int(page)
    if kind not in KINDS or region.upper() not in _region_codes() or page < 1:
        raise Http404
    # An empty page is cached as '' so a crawler asking past the end does not
    # run the query every time.
    xml = _cached(f'sitemap:{kind}:{region}:{page}', lambda: build_section(kind, region, page) or '')
    if not xml:
        raise Http404
    return _xml_response(xml)

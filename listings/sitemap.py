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

Pages are keyset-paginated. One pass over a kind and region (ids and dates
only) records the id each page starts at, and a page then reads just the rows
between its own start and the next page's, so its cost stays at one page's
worth however large the catalogue grows. An OFFSET would have re-read every
earlier page, and for books re-grouped the whole region, on each request.

Only what is for sale goes in: a book page is listed while at least one of its
region's listings is active, and a listing page while it is itself active. A
sold or removed listing drops out on the next rebuild instead of sending a
crawler to a page that has nothing left to buy.

A book for sale in more than one region names each region's page as an
hreflang alternate, in that region's default language, so a search engine
can send each visitor to the copy in their language.
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
XHTML_NS = 'http://www.w3.org/1999/xhtml'

# escape() leaves double quotes alone; an attribute value needs them escaped.
_ATTR = {'"': '&quot;'}


def _books(region_code):
    return (
        Book.objects
        .filter(region_id=region_code, listings__status='active')
        .annotate(lastmod=Max('listings__updated_at'))
        .order_by('id')
    )


def _listings(region_code):
    # Walks listing_region_status_id_idx in order.
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


def _book_alternates(rows):
    """{isbn13: [region codes]} for the books on this page for sale in more
    than one active region.

    Book rows are per region, so the same title in TW and HK is two rows
    sharing an ISBN; one query over the page's ISBNs finds the others. A
    book without an ISBN has no counterpart to find. The language of each
    region comes from the cached active-region map, so it costs no query.
    """
    isbns = [row['isbn13'] for row in rows if row['isbn13']]
    if not isbns:
        return {}
    regions = {}
    for isbn, region_code in (
        Book.objects
        .filter(isbn13__in=isbns, region_id__in=_region_codes(), listings__status='active')
        .values_list('isbn13', 'region_id')
        .distinct()
    ):
        regions.setdefault(isbn, []).append(region_code)
    # A book sold in one region only has no other language to point to.
    return {isbn: sorted(codes) for isbn, codes in regions.items() if len(codes) > 1}


def _alternate_links(origin, row, region_codes):
    """(hreflang, href) for each region's copy of a book page, then the
    bare path as x-default: the frontend forwards it to the visitor's
    region. The same set the book-page Function puts in its <head>."""
    if not region_codes:
        return []
    active = _get_active_regions()
    links = [
        (active[code].default_language_id, _book_loc(origin, code.lower(), row))
        for code in region_codes
    ]
    query = urlencode({'isbn': row['isbn13']})
    links.append(('x-default', f'{origin}/book?{query}'))
    return links


def _region_codes():
    return list(_get_active_regions())


def _xml_response(xml):
    response = HttpResponse(xml, content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = f'public, max-age={SITEMAP_TTL}'
    return response


def _cached(key, build):
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, SITEMAP_TTL)
    return value


def _build_pages(kind, region_code):
    """[(first id, newest lastmod)] for each page of one kind and region.

    The single pass over every row; it reads two columns, which is all the
    index needs to date each page and each page needs to find its rows.
    """
    queryset, _, _ = KINDS[kind]
    pages = []
    rows = queryset(region_code).values_list('id', 'lastmod')
    for i, (pk, stamp) in enumerate(rows.iterator()):
        if i % PAGE_SIZE == 0:
            pages.append([pk, stamp])
        elif stamp > pages[-1][1]:
            pages[-1][1] = stamp
    return [tuple(page) for page in pages]


def _pages(kind, region_code):
    return _cached(f'sitemap_pages:{kind}:{region_code}', lambda: _build_pages(kind, region_code))


def build_index(request):
    entries = []
    for region_code in _region_codes():
        region = region_code.lower()
        for kind in KINDS:
            for page, (_, lastmod) in enumerate(_pages(kind, region_code), start=1):
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


def build_section(kind, region, start, end):
    """The page holding ids from `start` up to, not including, `end`.

    Bounded on both sides rather than LIMITed, so two pages never share a
    row: a listing added since the bounds were taken joins the page its id
    falls in, and the page is at most a TTL's worth of additions over size.
    The first page has no `start` and the last no `end`, so an id below or
    above every bound still lands somewhere.
    """
    queryset, fields, loc = KINDS[kind]
    origin = settings.FRONTEND_URL.rstrip('/')
    rows = queryset(region.upper())
    if start is not None:
        rows = rows.filter(id__gte=start)
    if end is not None:
        rows = rows.filter(id__lt=end)
    rows = list(rows.values(*fields))
    alternates = _book_alternates(rows) if kind == 'books' else {}

    entries = []
    for row in rows:
        links = ''.join(
            f'<xhtml:link rel="alternate" hreflang="{lang}" href="{escape(href, _ATTR)}"/>'
            for lang, href in _alternate_links(origin, row, alternates.get(row.get('isbn13')))
        )
        entries.append(
            f'<url><loc>{escape(loc(origin, region, row))}</loc>'
            f'<lastmod>{row["lastmod"].date().isoformat()}</lastmod>'
            f'{links}</url>\n'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{XMLNS}" xmlns:xhtml="{XHTML_NS}">\n'
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
    if kind not in KINDS or region.upper() not in _region_codes():
        raise Http404
    pages = _pages(kind, region.upper())
    if not 1 <= page <= len(pages):
        raise Http404
    start = pages[page - 1][0] if page > 1 else None
    end = pages[page][0] if page < len(pages) else None
    # Keyed by the bounds too: once they are re-taken, a page built from the
    # old ones is not served against the new index.
    xml = _cached(
        f'sitemap:{kind}:{region}:{page}:{start}:{end}',
        lambda: build_section(kind, region, start, end),
    )
    return _xml_response(xml)

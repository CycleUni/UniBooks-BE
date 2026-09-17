"""Admin statistics.

Every endpoint answers for exactly one region, named by the required
`?region=` parameter. That is what keeps the numbers apart: an order's
`total_amount` is in its region's currency, so a TWD and an HKD amount must
never meet in the same Sum. Asking for one region at a time makes that
impossible by construction rather than by remembering to group by currency.

"Period" (`?days=`) always filters on `created_at`, so a completed order is
counted in the period it was *placed* — its completion can fall later, and
the figures for a period must not change once that period is over. Times to
completion use `completed_at`. A "transaction" is an order whose status is
`completed`.
"""

import bisect
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db.models import (
    Avg, Count, DurationField, Exists, ExpressionWrapper, F, FloatField,
    IntegerField, Max, Min, OuterRef, Q, Subquery, Sum,
)
from django.db.models.functions import Coalesce, Lower, Trim, TruncDate, TruncHour
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, views
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import RegionVerification, School, User
from ads.models import Ad
from catalog.models import Book
from core.i18n import resolve_language
from core.models import Category, Region
from listings.models import Listing
from messaging.models import Conversation
from moderation.models import ChatReport, Report
from orders.models import Order, Review
from subscriptions.models import Subscription

from ..pagination import AdminPagination
from ..permissions import IsRegionManager

# 1 is "today": from local midnight, like every other window here.
ALLOWED_DAYS = (1, 7, 30, 90, 365, 0)
DEFAULT_DAYS = 30
# `days=0` means all time for totals, but a chart of every day since launch is
# neither cheap nor readable, so the series stops at a year.
MAX_SERIES_DAYS = 365
ORDER_STATUSES = [s for s, _ in Order.STATUS_CHOICES]
LISTING_STATUSES = [s for s, _ in Listing.STATUS_CHOICES]
SORT_FIELDS = {'completed': 'completed_count', 'gmv': 'gmv', 'orders': 'order_count'}
# Overview and series are whole-region aggregates that every admin of the
# region sees identically, so a short TTL is safe; access is checked before
# the cache is read.
STATS_CACHE_TTL = 60


# ---------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------

def _invalid():
    return ValidationError({"error": {"code": "admin.errInvalidField"}})


def resolve_stats_region(request):
    """The one region this request is about, after checking the caller may see it."""
    code = (request.query_params.get('region') or '').strip().upper()
    if not code:
        raise _invalid()
    region = Region.objects.select_related('currency').filter(pk=code).first()
    if region is None:
        raise _invalid()
    user = request.user
    if not user.is_superuser and not user.managed_regions.filter(pk=region.pk).exists():
        raise PermissionDenied({"error": {"code": "admin.errRegionForbidden"}})
    return region


def parse_days(request):
    raw = request.query_params.get('days')
    if raw in (None, ''):
        return DEFAULT_DAYS
    try:
        days = int(raw)
    except ValueError:
        raise _invalid()
    if days not in ALLOWED_DAYS:
        raise _invalid()
    return days


def _optional_int(request, name):
    raw = request.query_params.get(name)
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except ValueError:
        raise _invalid()


def _tz(region):
    try:
        return ZoneInfo(region.timezone)
    except Exception:
        return timezone.get_current_timezone()


def period_start(region, days):
    """Start of the first local day in the window; None for all time."""
    if not days:
        return None
    tz = _tz(region)
    first_day = timezone.now().astimezone(tz).date() - timedelta(days=days - 1)
    return datetime.combine(first_day, time.min, tzinfo=tz)


def _since(qs, since, field='created_at'):
    return qs.filter(**{f'{field}__gte': since}) if since else qs


def _ratio(num, den, digits=4):
    return round(num / den, digits) if den else None


def _round_int(value):
    return int(round(value)) if value is not None else None


# ---------------------------------------------------------------------
# Chart series
# ---------------------------------------------------------------------

class SeriesBuckets:
    """The chart's time buckets for a period, in the region's local time.

    Today is one day, and a one-bar chart says nothing, so it is split into
    hours — up to the current one, since hours that have not happened yet
    would read as hours with no activity. Every other period is one bucket
    per day, zero-filled, capped at MAX_SERIES_DAYS.

    Each bucket's label goes out in the `date` field: `2026-09-17` for a day,
    `14:00` for an hour. `unit` tells the client which it is.
    """

    def __init__(self, region, days):
        self.tz = _tz(region)
        now = timezone.now().astimezone(self.tz)
        if days == 1:
            self.unit = 'hour'
            self.keys = list(range(now.hour + 1))
            self.labels = [f'{h:02d}:00' for h in self.keys]
            self.start = datetime.combine(now.date(), time.min, tzinfo=self.tz)
        else:
            self.unit = 'day'
            n = days if days and days <= MAX_SERIES_DAYS else MAX_SERIES_DAYS
            self.keys = [now.date() - timedelta(days=i) for i in range(n - 1, -1, -1)]
            self.labels = [d.isoformat() for d in self.keys]
            self.start = datetime.combine(self.keys[0], time.min, tzinfo=self.tz)

    def group(self, qs, **aggregates):
        """{bucket key: {aggregate: value}} for rows of `qs` inside the series."""
        trunc = TruncHour if self.unit == 'hour' else TruncDate
        rows = (
            qs.filter(created_at__gte=self.start)
            .order_by()
            .annotate(bucket=trunc('created_at', tzinfo=self.tz))
            .values('bucket')
            .annotate(**aggregates)
        )
        return {self._key(row['bucket']): row for row in rows}

    def _key(self, value):
        if self.unit == 'hour':
            return value.astimezone(self.tz).hour if value.tzinfo else value.hour
        return value

    def rows(self):
        return zip(self.keys, self.labels)


def _order_series(buckets, orders, with_gmv=True):
    aggs = {
        'orders': Count('pk'),
        'completed': Count('pk', filter=Q(status='completed')),
    }
    if with_gmv:
        aggs['gmv'] = Sum('total_amount', filter=Q(status='completed'))
    grouped = buckets.group(orders, **aggs)
    out = []
    for key, label in buckets.rows():
        row = grouped.get(key, {})
        item = {'date': label, 'orders': row.get('orders', 0), 'completed': row.get('completed', 0)}
        if with_gmv:
            item['gmv'] = row.get('gmv') or 0
        out.append(item)
    return out


# ---------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------

def _region_orders(region):
    return Order.objects.filter(region=region)


def _top_schools(orders, lang, with_gmv=True, limit=10):
    rows = (
        orders.filter(status='completed', listing__school__isnull=False)
        .order_by()
        .values('listing__school')
        .annotate(completed_orders=Count('pk'), gmv=Sum('total_amount'))
        .order_by('-completed_orders', '-gmv', 'listing__school')[:limit]
    )
    rows = list(rows)
    schools = School.objects.in_bulk([r['listing__school'] for r in rows])
    out = []
    for r in rows:
        school = schools.get(r['listing__school'])
        item = {
            'id': r['listing__school'],
            'name': school.localized_name(lang) if school else '',
            'completed_orders': r['completed_orders'],
        }
        if with_gmv:
            item['gmv'] = r['gmv'] or 0
        out.append(item)
    return out


def _book_dict(book, full=False):
    data = {
        'id': book.pk,
        'title': book.title,
        'authors': book.authors,
        'isbn13': book.isbn13,
        'cover_url': book.cover_url,
    }
    if full:
        data['publisher'] = book.publisher
        data['published_date'] = book.published_date
    return data


def _per_book(qs, expr, output_field):
    """Correlated subquery: `expr` aggregated over `qs` for the outer Book."""
    sub = (
        qs.filter(listing__book=OuterRef('pk'))
        .order_by()
        .values('listing__book')
        .annotate(v=expr)
        .values('v')[:1]
    )
    return Subquery(sub, output_field=output_field)


def _count_per_book(model_qs, book_field='book'):
    sub = (
        model_qs.filter(**{book_field: OuterRef('pk')})
        .order_by()
        .values(book_field)
        .annotate(c=Count('pk'))
        .values('c')[:1]
    )
    return Coalesce(Subquery(sub, output_field=IntegerField()), 0)


def listing_scope(request, region):
    """Listings narrowed by ?school= ?category= ?course= ?professor=, or None.

    `school` and `category` take an id, or `none` for listings without one.
    `course` and `professor` take the grouping key the breakdown returns — the
    trimmed, lower-cased value as the database computed it — and are matched
    against the same expression, so a row always finds its own listings.
    """
    listings = Listing.objects.filter(region=region)
    scoped = False
    for name in ('school', 'category'):
        raw = request.query_params.get(name)
        if raw in (None, ''):
            continue
        scoped = True
        if raw == 'none':
            listings = listings.filter(**{f'{name}__isnull': True})
            continue
        try:
            listings = listings.filter(**{f'{name}_id': int(raw)})
        except ValueError:
            raise _invalid()
    for name, field in TEXT_FIELDS.items():
        raw = request.query_params.get(name)
        if not raw:
            continue
        scoped = True
        listings = listings.annotate(**{f'_{name}_key': Lower(Trim(field))}).filter(**{f'_{name}_key': raw})
    return listings if scoped else None


def annotate_book_stats(books, region, orders, scope=None):
    """`orders` must already be narrowed to `scope`; active listings are narrowed here."""
    completed = orders.filter(status='completed')
    listings = Listing.objects.filter(region=region, status='active')
    if scope is not None:
        listings = listings.filter(pk__in=scope.values('pk'))
    return books.annotate(
        order_count=Coalesce(_per_book(orders, Count('pk'), IntegerField()), 0),
        completed_count=Coalesce(_per_book(completed, Count('pk'), IntegerField()), 0),
        cancelled_count=Coalesce(
            _per_book(orders.filter(status='cancelled'), Count('pk'), IntegerField()), 0
        ),
        gmv=Coalesce(_per_book(completed, Sum('total_amount'), IntegerField()), 0),
        avg_price=_per_book(completed, Avg('total_amount'), FloatField()),
        min_price=_per_book(completed, Min('total_amount'), IntegerField()),
        max_price=_per_book(completed, Max('total_amount'), IntegerField()),
        active_listings=_count_per_book(listings),
    )


def book_stats_dict(book):
    return {
        'completed_count': book.completed_count,
        'order_count': book.order_count,
        'cancelled_count': book.cancelled_count,
        'gmv': book.gmv,
        'avg_price': _round_int(book.avg_price),
        'min_price': book.min_price,
        'max_price': book.max_price,
        'active_listings': book.active_listings,
    }


class CompetitionRank:
    """Competition rank ("1, 2, 2, 4") among a fixed set of values.

    Built once from every value in the ranked set, so the rank of any book —
    including one found by search, which a page position cannot give — costs
    nothing more. A zero value is unranked.
    """

    def __init__(self, values):
        self._values = sorted(v for v in values if v > 0)

    def rank(self, value):
        if not value:
            return None
        return len(self._values) - bisect.bisect_right(self._values, value) + 1


class BookRanker(CompetitionRank):
    """Books ranked by a transaction figure over the period's orders."""

    def __init__(self, orders, sort_field):
        rows = (
            orders.order_by()
            .values('listing__book')
            .annotate(
                order_count=Count('pk'),
                completed_count=Count('pk', filter=Q(status='completed')),
                gmv=Coalesce(Sum('total_amount', filter=Q(status='completed')), 0),
            )
        )
        super().__init__(r[sort_field] for r in rows)


# ---------------------------------------------------------------------
# Book requests (Subscription)
#
# Kept apart from transactions on purpose. A request is unmet demand: the
# book page only offers it while nothing is listed, and the row outlives the
# listing that answers it (it is marked notified, not removed). Shown beside
# sales figures it reads as people still waiting when most of them have been
# told, and may well have bought.
# ---------------------------------------------------------------------

def _region_requests(region):
    return Subscription.objects.filter(region=region)


def _active_listings(region):
    return Listing.objects.filter(region=region, status='active')


def annotate_request_stats(books, region, since):
    requests = _region_requests(region)
    return books.annotate(
        request_count=_count_per_book(requests),
        new_requests=_count_per_book(_since(requests, since)),
        notified_count=_count_per_book(requests.filter(notified_at__isnull=False)),
        active_listings=_count_per_book(_active_listings(region)),
        last_requested_at=Subquery(
            requests.filter(book=OuterRef('pk')).order_by('-created_at').values('created_at')[:1]
        ),
    )


def request_stats_dict(book):
    return {
        'request_count': book.request_count,
        'new_requests': book.new_requests,
        'notified_count': book.notified_count,
        'waiting_count': book.request_count - book.notified_count,
        'active_listings': book.active_listings,
        'last_requested_at': book.last_requested_at.isoformat() if book.last_requested_at else None,
    }


def request_ranker(region, unlisted_only=False):
    books = Book.objects.filter(Exists(_region_requests(region).filter(book=OuterRef('pk'))))
    books = annotate_request_stats(books, region, None)
    if unlisted_only:
        books = books.filter(active_listings=0)
    return CompetitionRank(books.values_list('request_count', flat=True))


# ---------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------

class AdminStatsOverviewView(views.APIView):
    """GET /api/v1/admin/stats/overview/?region=TW&days=30"""
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        lang = resolve_language(request)
        key = f'admin_stats_overview:{region.pk}:{days}:{lang}'
        data = cache.get(key)
        if data is None:
            data = self._build(region, days, lang)
            cache.set(key, data, STATS_CACHE_TTL)
        return Response(data)

    @staticmethod
    def _requests(region, since):
        requests = _region_requests(region)
        unlisted = requests.exclude(
            Exists(_active_listings(region).filter(book=OuterRef('book')))
        )
        return {
            'total': requests.count(),
            'new': _since(requests, since).count(),
            'requested_books': requests.order_by().values('book').distinct().count(),
            'unlisted_books': unlisted.order_by().values('book').distinct().count(),
        }

    def _build(self, region, days, lang):
        since = period_start(region, days)
        orders_all = _region_orders(region)
        orders = _since(orders_all, since)

        # Users: whoever holds an active verification in this region.
        region_users = User.objects.filter(
            deleted_at__isnull=True,
            pk__in=RegionVerification.objects.active_in(region).values('user'),
        )
        listings_all = Listing.objects.filter(region=region)
        listings = _since(listings_all, since)

        # Orders
        by_status = dict.fromkeys(ORDER_STATUSES, 0)
        for row in orders.order_by().values('status').annotate(c=Count('pk')):
            by_status[row['status']] = row['c']
        completed = orders.filter(status='completed')
        completed_agg = completed.aggregate(
            gmv=Sum('total_amount'),
            avg=Avg('total_amount'),
            duration=Avg(
                ExpressionWrapper(F('completed_at') - F('created_at'), output_field=DurationField()),
                filter=Q(completed_at__isnull=False),
            ),
        )
        done = by_status['completed'] + by_status['cancelled']
        duration = completed_agg['duration']
        cancel_reasons = [
            {'reason': r['cancel_reason'] or '', 'count': r['c']}
            for r in orders.filter(status='cancelled').order_by()
            .values('cancel_reason').annotate(c=Count('pk')).order_by('-c', 'cancel_reason')
        ]

        # Listings
        listing_status = dict.fromkeys(LISTING_STATUSES, 0)
        for row in listings_all.order_by().values('status').annotate(c=Count('pk')):
            listing_status[row['status']] = row['c']

        # Moderation
        listing_reports = Report.objects.filter(listing__region=region)
        chat_reports = ChatReport.objects.filter(conversation__listing__region=region)
        reasons = {}
        for qs in (_since(listing_reports, since), _since(chat_reports, since)):
            for row in qs.order_by().values('reason').annotate(c=Count('pk')):
                reasons[row['reason']] = reasons.get(row['reason'], 0) + row['c']

        # Reviews on this region's orders
        review_agg = _since(Review.objects.filter(order__region=region), since).aggregate(
            count=Count('pk'),
            avg=Avg('rating', filter=Q(is_no_show=False)),
            no_show=Count('pk', filter=Q(is_no_show=True)),
        )

        # Ads shown in this region. Counters are lifetime totals: views and
        # clicks are plain integers on Ad with no per-day history.
        now = timezone.now()
        region_ads = Ad.objects.filter(
            pk__in=Ad.objects.filter(
                Q(advertiser__all_regions=True) | Q(advertiser__regions=region)
            ).values('pk')
        )
        ads_agg = region_ads.aggregate(
            active=Count('pk', filter=Q(
                is_active=True, advertiser__is_active=True,
                start_date__lte=now, end_date__gte=now,
            )),
            views=Coalesce(Sum('views_count'), 0),
            clicks=Coalesce(Sum('clicks_count'), 0),
        )

        return {
            'region': region.pk,
            'currency': region.currency_id,
            'days': days,
            'since': since.isoformat() if since else None,
            'users': {
                'total': region_users.count(),
                'new': _since(region_users, since).count(),
                'active_sellers': listings.order_by().values('seller').distinct().count(),
                'active_buyers': orders.order_by().values('buyer').distinct().count(),
            },
            'listings': {
                'new': listings.count(),
                'by_status': listing_status,
                'avg_price': _round_int(
                    listings_all.filter(status='active').aggregate(v=Avg('price'))['v']
                ),
            },
            'orders': {
                'new': sum(by_status.values()),
                'by_status': by_status,
                'completion_rate': _ratio(by_status['completed'], done),
                'cancel_rate': _ratio(by_status['cancelled'], done),
                'gmv': completed_agg['gmv'] or 0,
                'avg_order_value': _round_int(completed_agg['avg']),
                'avg_days_to_complete': (
                    round(duration.total_seconds() / 86400, 1) if duration is not None else None
                ),
                'cancel_reasons': cancel_reasons,
            },
            'engagement': {
                # Conversation has no created_at, so this is a current total,
                # not a count for the period.
                'conversations': Conversation.objects.filter(listing__region=region).count(),
            },
            'requests': self._requests(region, since),
            'moderation': {
                'open_listing_reports': listing_reports.filter(status='open').count(),
                'open_chat_reports': chat_reports.filter(status='open').count(),
                'new_listing_reports': _since(listing_reports, since).count(),
                'new_chat_reports': _since(chat_reports, since).count(),
                'report_reasons': [
                    {'reason': k, 'count': v}
                    for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
            },
            'reviews': {
                'count': review_agg['count'],
                'avg_rating': round(review_agg['avg'], 2) if review_agg['avg'] is not None else None,
                'no_show_count': review_agg['no_show'],
            },
            'ads': {
                'active': ads_agg['active'],
                'views': ads_agg['views'],
                'clicks': ads_agg['clicks'],
                'ctr': _ratio(ads_agg['clicks'], ads_agg['views']),
            },
            'top_schools': _top_schools(orders, lang),
        }


class AdminStatsTimeseriesView(views.APIView):
    """GET /api/v1/admin/stats/timeseries/?region=TW&days=30"""
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        key = f'admin_stats_series:{region.pk}:{days}'
        data = cache.get(key)
        if data is None:
            data = self._build(region, days)
            cache.set(key, data, STATS_CACHE_TTL)
        return Response(data)

    def _build(self, region, days):
        buckets = SeriesBuckets(region, days)
        series = _order_series(buckets, _region_orders(region))
        users = buckets.group(
            User.objects.filter(
                deleted_at__isnull=True,
                pk__in=RegionVerification.objects.active_in(region).values('user'),
            ),
            c=Count('pk'),
        )
        listings = buckets.group(Listing.objects.filter(region=region), c=Count('pk'))
        for key, item in zip(buckets.keys, series):
            item['new_users'] = users.get(key, {}).get('c', 0)
            item['new_listings'] = listings.get(key, {}).get('c', 0)
        return {
            'region': region.pk,
            'currency': region.currency_id,
            'days': days,
            'unit': buckets.unit,
            'series': series,
        }


class AdminStatsBookRankingView(generics.GenericAPIView):
    """GET /api/v1/admin/stats/books/ranking/?region=TW&days=30&sort=completed

    Narrow to one breakdown row with school / category / course / professor
    (see listing_scope): the books traded in that school, college or course.

    Without `q`, only books ordered in the period are listed. With `q`, any
    matching book in the region is returned — including ones never sold — so
    an admin can look up a specific title.
    """
    permission_classes = [IsAdminUser, IsRegionManager]
    pagination_class = AdminPagination

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        sort = request.query_params.get('sort') or 'completed'
        if sort not in SORT_FIELDS:
            raise _invalid()
        sort_field = SORT_FIELDS[sort]
        scope = listing_scope(request, region)
        q = (request.query_params.get('q') or '').strip()

        orders = _since(_region_orders(region), period_start(region, days))
        if scope is not None:
            orders = orders.filter(listing__in=scope.values('pk'))

        books = Book.objects.all()
        if q:
            isbn = q.replace('-', '').replace(' ', '')
            books = books.filter(
                Q(title__icontains=q) | Q(authors__icontains=q) | Q(isbn13__icontains=isbn)
            ).filter(
                Q(region=region)
                | Exists(Listing.objects.filter(region=region, book=OuterRef('pk')))
            )
        else:
            books = books.filter(Exists(orders.filter(listing__book=OuterRef('pk'))))

        books = annotate_book_stats(books, region, orders, scope).order_by(
            F(sort_field).desc(), '-completed_count', '-gmv', 'title', 'pk',
        )

        page = self.paginate_queryset(books)
        ranker = BookRanker(orders, sort_field)
        results = [
            {
                'rank': ranker.rank(getattr(book, sort_field)),
                'book': _book_dict(book),
                **book_stats_dict(book),
            }
            for book in page
        ]
        response = self.get_paginated_response(results)
        response.data['currency'] = region.currency_id
        response.data['sort'] = sort
        return response


class AdminStatsBookRequestsView(generics.GenericAPIView):
    """GET /api/v1/admin/stats/books/requests/?region=TW&days=30&unlisted=1&q=

    Books people have asked for, most requested first. `unlisted=1` keeps only
    books nobody is selling right now — the requests still unanswered. `days`
    only bounds the "new requests" column: a request counts until it is
    withdrawn, however old it is. Rank is taken within the same `unlisted`
    selection and is unaffected by `q`.
    """
    permission_classes = [IsAdminUser, IsRegionManager]
    pagination_class = AdminPagination

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        unlisted_only = (request.query_params.get('unlisted') or '').lower() in ('1', 'true')
        q = (request.query_params.get('q') or '').strip()

        books = annotate_request_stats(
            Book.objects.filter(Exists(_region_requests(region).filter(book=OuterRef('pk')))),
            region, period_start(region, days),
        )
        if unlisted_only:
            books = books.filter(active_listings=0)
        if q:
            isbn = q.replace('-', '').replace(' ', '')
            books = books.filter(
                Q(title__icontains=q) | Q(authors__icontains=q) | Q(isbn13__icontains=isbn)
            )
        books = books.order_by('-request_count', '-new_requests', 'title', 'pk')

        page = self.paginate_queryset(books)
        ranker = request_ranker(region, unlisted_only)
        results = [
            {
                'rank': ranker.rank(book.request_count),
                'book': _book_dict(book),
                **request_stats_dict(book),
            }
            for book in page
        ]
        response = self.get_paginated_response(results)
        response.data['summary'] = AdminStatsOverviewView._requests(region, period_start(region, days))
        return response


# ---------------------------------------------------------------------
# Breakdown by school / college category / course / professor
# ---------------------------------------------------------------------

BREAKDOWN_DIMENSIONS = ('school', 'category', 'course', 'professor')
BREAKDOWN_SORTS = ('completed', 'gmv', 'orders', 'new_listings', 'active_listings')
# Course and professor are free text typed by sellers; these are the fields.
TEXT_FIELDS = {'course': 'course_name', 'professor': 'professor_name'}


class AdminStatsBreakdownView(views.APIView):
    """GET /api/v1/admin/stats/breakdown/?region=TW&days=30&by=course&school=3

    Listings and transactions grouped by one dimension:

    - `school`, `category`: the foreign key. Listings without one form their
      own row (id null), since "not filled in" is itself worth seeing.
    - `course`, `professor`: free text, so rows are grouped on the trimmed,
      case-folded value — "Calculus " and "calculus" are one course. The label
      shown is one of the spellings actually used. Blank values are left out
      and reported as coverage in `summary` instead.

    `school` narrows every dimension but `school` itself. A row is listed when
    anything happened in the period or it still has active listings. Rank is
    taken over all rows, before `q` narrows them.
    """
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        lang = resolve_language(request)
        since = period_start(region, days)
        by = request.query_params.get('by') or 'school'
        sort = request.query_params.get('sort') or 'completed'
        if by not in BREAKDOWN_DIMENSIONS or sort not in BREAKDOWN_SORTS:
            raise _invalid()
        school = _optional_int(request, 'school') if by != 'school' else None
        q = (request.query_params.get('q') or '').strip().casefold()
        page = max(1, _optional_int(request, 'page') or 1)
        page_size = min(100, max(1, _optional_int(request, 'page_size') or 20))

        listings = Listing.objects.filter(region=region)
        orders = _since(_region_orders(region), since)
        if school:
            listings = listings.filter(school_id=school)
            orders = orders.filter(listing__school_id=school)

        text_field = TEXT_FIELDS.get(by)
        if text_field:
            listing_key = Lower(Trim(text_field))
            order_key = Lower(Trim(f'listing__{text_field}'))
        else:
            listing_key = F(f'{by}_id')
            order_key = F(f'listing__{by}_id')

        new_filter = Q(created_at__gte=since) if since else Q()
        listing_aggs = {
            'active_listings': Count('pk', filter=Q(status='active')),
            'new_listings': Count('pk', filter=new_filter),
        }
        if text_field:
            listing_aggs['label'] = Min(Trim(text_field))

        rows = {}
        for r in listings.order_by().annotate(k=listing_key).values('k').annotate(**listing_aggs):
            if text_field and not r['k']:
                continue
            rows[r['k']] = {
                'key': r['k'], 'label': r.get('label') or '',
                'active_listings': r['active_listings'], 'new_listings': r['new_listings'],
                'orders': 0, 'completed': 0, 'gmv': 0,
            }
        for r in orders.order_by().annotate(k=order_key).values('k').annotate(
            n=Count('pk'),
            done=Count('pk', filter=Q(status='completed')),
            gmv=Coalesce(Sum('total_amount', filter=Q(status='completed')), 0),
        ):
            row = rows.get(r['k'])
            if row is None:  # every order has a listing, so this is only a blank text key
                continue
            row.update(orders=r['n'], completed=r['done'], gmv=r['gmv'])

        rows = [
            r for r in rows.values()
            if r['active_listings'] or r['new_listings'] or r['orders']
        ]
        self._label(by, rows, lang)

        ranker = CompetitionRank(r[sort] for r in rows)
        for r in rows:
            r['rank'] = ranker.rank(r[sort])
            if not text_field:
                r['id'] = r.pop('key')
            # A text row keeps `key`: passed back as ?course= / ?professor=,
            # it selects exactly this row's listings.
        if q:
            rows = [r for r in rows if q in r['_search']]
        for r in rows:
            del r['_search']
        rows.sort(key=lambda r: (-r[sort], -r['completed'], -r['new_listings'], r['label']))

        summary = {'groups': len(rows)}
        if text_field:
            summary['listings'] = listings.count()
            summary['listings_with_value'] = (
                listings.annotate(v=Trim(text_field)).exclude(v='').count()
            )

        start = (page - 1) * page_size
        return Response({
            'region': region.pk,
            'currency': region.currency_id,
            'days': days,
            'by': by,
            'sort': sort,
            'count': len(rows),
            'summary': summary,
            'results': rows[start:start + page_size],
        })

    @staticmethod
    def _label(by, rows, lang):
        """Display labels, plus the text `q` matches: the shown name and the
        canonical one, so a search in either language finds the row."""
        keys = [r['key'] for r in rows]
        if by == 'school':
            names = {s.pk: (s.localized_name(lang), s.name) for s in School.objects.filter(pk__in=keys)}
        elif by == 'category':
            names = {c.pk: (c.localized(lang)['title'], c.title) for c in Category.objects.filter(pk__in=keys)}
        else:
            names = None
        for r in rows:
            if names is not None:
                r['label'], canonical = names.get(r['key'], ('', ''))
            else:
                canonical = ''
            r['_search'] = f"{r['label']} {canonical}".casefold()


class AdminStatsBookDetailView(views.APIView):
    """GET /api/v1/admin/stats/books/<id>/?region=TW&days=30"""
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request, pk):
        region = resolve_stats_region(request)
        days = parse_days(request)
        lang = resolve_language(request)
        since = period_start(region, days)

        region_orders = _region_orders(region)
        orders = _since(region_orders, since)
        book = get_object_or_404(annotate_book_stats(Book.objects.all(), region, orders), pk=pk)
        book_orders = orders.filter(listing__book=book)
        all_book_orders = region_orders.filter(listing__book=book)

        by_status = dict.fromkeys(ORDER_STATUSES, 0)
        for row in book_orders.order_by().values('status').annotate(c=Count('pk')):
            by_status[row['status']] = row['c']

        summary = book_stats_dict(book)
        summary['all_time_completed'] = all_book_orders.filter(status='completed').count()
        summary['rank'] = BookRanker(orders, 'completed_count').rank(book.completed_count)

        recent_orders = [
            {
                'id': str(o.id),
                'status': o.status,
                'total_amount': o.total_amount,
                'school_name': o.listing.school.localized_name(lang) if o.listing.school else '',
                'created_at': o.created_at.isoformat(),
            }
            for o in all_book_orders.select_related('listing__school').order_by('-created_at')[:10]
        ]
        active_listings = [
            {
                'id': str(l.id),
                'price': l.price,
                'condition': l.condition,
                'school_name': l.school.localized_name(lang) if l.school else '',
                'created_at': l.created_at.isoformat(),
            }
            for l in Listing.objects.filter(region=region, book=book, status='active')
            .select_related('school').order_by('price', '-created_at')[:20]
        ]

        series_buckets = SeriesBuckets(region, days)
        requested = annotate_request_stats(Book.objects.filter(pk=book.pk), region, since).get()
        requests = request_stats_dict(requested)
        del requests['active_listings']  # already in summary
        requests['rank'] = request_ranker(region).rank(requested.request_count)

        return Response({
            'region': region.pk,
            'currency': region.currency_id,
            'days': days,
            'book': _book_dict(book, full=True),
            'summary': summary,
            'requests': requests,
            'by_status': by_status,
            'series_unit': series_buckets.unit,
            'series': _order_series(series_buckets, all_book_orders, with_gmv=False),
            'top_schools': _top_schools(orders.filter(listing__book=book), lang, with_gmv=False),
            'recent_orders': recent_orders,
            'active_listings': active_listings,
        })

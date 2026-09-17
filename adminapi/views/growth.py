"""Growth, liquidity and retention statistics for the admin.

Same ground rules as stats.py: one region per request, `?days=` filters on
creation time. Everything here is computed from the platform's own tables;
figures that need behaviour the database never sees (searches, page views,
traffic sources) are left to Google Analytics.
"""

import statistics

from django.core.cache import cache
from django.db.models import Exists, Min, OuterRef
from django.utils import timezone
from rest_framework import views
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from listings.models import Listing
from messaging.models import Conversation
from orders.models import Order
from subscriptions.models import Subscription

from ..permissions import IsRegionManager
from .stats import (
    STATS_CACHE_TTL, _invalid, _optional_int, _ratio, _since, _tz, parse_days, period_start,
    resolve_stats_region,
)

DAY_SECONDS = 86400


def _durations(pairs):
    """{count, avg_days, median_days} for (start, end) pairs; pairs with a gap are skipped."""
    days = [
        (end - start).total_seconds() / DAY_SECONDS
        for start, end in pairs
        if start is not None and end is not None and end >= start
    ]
    if not days:
        return {'count': 0, 'avg_days': None, 'median_days': None}
    return {
        'count': len(days),
        'avg_days': round(sum(days) / len(days), 1),
        'median_days': round(statistics.median(days), 1),
    }


class AdminStatsGrowthView(views.APIView):
    """GET /api/v1/admin/stats/growth/?region=TW&days=90

    Marketplace health for the period:

    - sell_through: listings created in the period that are now sold.
    - speed: listing → first chat, listing → first order, listing → sale,
      order → completion (days; average and median).
    - users: buyers, sellers, both (overlap), repeat buyers.
    - chat_to_order: chats that led to an order by the same buyer on the same
      listing. Chats opened before their start time was recorded have no
      date, so with a period set only dated chats count.
    - requests: requests made in the period, how many were notified, and how
      many ended with the requester ordering that book afterwards.
    """
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request):
        region = resolve_stats_region(request)
        days = parse_days(request)
        key = f'admin_stats_growth:{region.pk}:{days}'
        data = cache.get(key)
        if data is None:
            data = self._build(region, days)
            cache.set(key, data, STATS_CACHE_TTL)
        return Response(data)

    def _build(self, region, days):
        since = period_start(region, days)
        listings = _since(Listing.objects.filter(region=region), since)
        orders_all = Order.objects.filter(region=region)
        orders = _since(orders_all, since)

        # Liquidity
        listing_total = listings.count()
        listing_sold = listings.filter(status='sold').count()

        timed_listings = listings.annotate(
            first_chat=Min('conversations__created_at'),
            first_order=Min('orders__created_at'),
        ).values_list('created_at', 'first_chat', 'first_order')
        timed_listings = list(timed_listings)

        sales = list(
            orders.filter(status='completed', completed_at__isnull=False)
            .values_list('listing__created_at', 'created_at', 'completed_at')
        )

        # People
        buyer_counts = {}
        for buyer in orders.values_list('buyer_id', flat=True):
            buyer_counts[buyer] = buyer_counts.get(buyer, 0) + 1
        buyers = set(buyer_counts)
        sellers = set(orders.values_list('seller_id', flat=True)) | set(listings.values_list('seller_id', flat=True))

        # Chats → orders
        chats = Conversation.objects.filter(listing__region=region)
        if since:
            chats = chats.filter(created_at__gte=since)
        ordered = Order.objects.filter(listing=OuterRef('listing'), buyer=OuterRef('buyer'))
        chats = chats.annotate(
            has_order=Exists(ordered),
            has_sale=Exists(ordered.filter(status='completed')),
        )
        chat_total = chats.count()
        chat_ordered = chats.filter(has_order=True).count()
        chat_sold = chats.filter(has_sale=True).count()
        undated_chats = Conversation.objects.filter(listing__region=region, created_at__isnull=True).count()

        # Requests → orders by the requester, placed after the request
        requests = _since(Subscription.objects.filter(region=region), since)
        requester_order = orders_all.filter(
            buyer=OuterRef('user'), listing__book=OuterRef('book'), created_at__gte=OuterRef('created_at'),
        )
        requests = requests.annotate(
            ordered=Exists(requester_order),
            bought=Exists(requester_order.filter(status='completed')),
        )
        request_total = requests.count()

        return {
            'region': region.pk,
            'days': days,
            'since': since.isoformat() if since else None,
            'sell_through': {
                'listings': listing_total,
                'sold': listing_sold,
                'rate': _ratio(listing_sold, listing_total),
            },
            'speed': {
                'listing_to_first_chat': _durations((c, chat) for c, chat, _ in timed_listings),
                'listing_to_first_order': _durations((c, order) for c, _, order in timed_listings),
                'listing_to_sale': _durations((listed, done) for listed, _, done in sales),
                'order_to_completion': _durations((placed, done) for _, placed, done in sales),
            },
            'users': {
                'buyers': len(buyers),
                'sellers': len(sellers),
                'both': len(buyers & sellers),
                'overlap_rate': _ratio(len(buyers & sellers), len(buyers | sellers)),
                'repeat_buyers': sum(1 for n in buyer_counts.values() if n >= 2),
                'repeat_rate': _ratio(sum(1 for n in buyer_counts.values() if n >= 2), len(buyers)),
            },
            'chat_to_order': {
                'chats': chat_total,
                'ordered': chat_ordered,
                'completed': chat_sold,
                'order_rate': _ratio(chat_ordered, chat_total),
                'completion_rate': _ratio(chat_sold, chat_total),
                # Chats from before start times were recorded: left out when a
                # period is chosen, since their date is unknown.
                'undated': undated_chats,
            },
            'requests': {
                'requests': request_total,
                'notified': requests.filter(notified_at__isnull=False).count(),
                'ordered': requests.filter(ordered=True).count(),
                'bought': requests.filter(bought=True).count(),
                'order_rate': _ratio(requests.filter(ordered=True).count(), request_total),
            },
        }


# ---------------------------------------------------------------------
# Semester cohorts
# ---------------------------------------------------------------------

def term_index(moment, tz):
    """A comparable number for the academic term a moment falls in.

    Two terms a year, as Taiwanese universities run them: autumn from August
    to January, spring from February to July. Hong Kong's calendar differs by
    a few weeks at each end; the same split is used there so terms line up
    across regions, and the page says so.
    """
    local = moment.astimezone(tz)
    year, month = local.year, local.month
    if month >= 8:
        return year * 2 + 1          # autumn of `year`
    if month == 1:
        return (year - 1) * 2 + 1    # still last year's autumn
    return year * 2                  # spring of `year`


def term_label(index):
    return {'year': index // 2, 'season': 'autumn' if index % 2 else 'spring'}


ROLES = ('all', 'buyer', 'seller')


class AdminStatsRetentionView(views.APIView):
    """GET /api/v1/admin/stats/retention/?region=TW&terms=6&role=all

    Semester cohorts. A user joins the cohort of the first term they were
    active in, and counts as retained in a later term when active again.
    Active means: placed an order (buyer), or listed a book or had one ordered
    (seller); `role` picks which. `retained[k]` is the cohort's size still
    active k terms later; the list stops at the current term.
    """
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request):
        region = resolve_stats_region(request)
        terms = _optional_int(request, 'terms') or 6
        role = request.query_params.get('role') or 'all'
        if not 1 <= terms <= 8 or role not in ROLES:
            raise _invalid()
        key = f'admin_stats_retention:{region.pk}:{terms}:{role}'
        data = cache.get(key)
        if data is None:
            data = self._build(region, terms, role)
            cache.set(key, data, STATS_CACHE_TTL)
        return Response(data)

    def _build(self, region, terms, role):
        tz = _tz(region)
        events = []
        if role in ('all', 'buyer'):
            events += Order.objects.filter(region=region).values_list('buyer_id', 'created_at')
        if role in ('all', 'seller'):
            events += Listing.objects.filter(region=region).values_list('seller_id', 'created_at')
            events += Order.objects.filter(region=region).values_list('seller_id', 'created_at')

        active = {}
        for user, moment in events:
            active.setdefault(user, set()).add(term_index(moment, tz))

        current = term_index(timezone.now(), tz)
        first_term = current - terms + 1
        cohorts = []
        for term in range(first_term, current + 1):
            members = [seen for seen in active.values() if min(seen) == term]
            size = len(members)
            retained = [
                sum(1 for seen in members if term + k in seen)
                for k in range(current - term + 1)
            ]
            cohorts.append({
                'term': term_label(term),
                'size': size,
                'retained': retained,
                'rates': [_ratio(n, size) for n in retained],
            })
        return {
            'region': region.pk,
            'role': role,
            'terms': [term_label(t) for t in range(first_term, current + 1)],
            'cohorts': cohorts,
        }

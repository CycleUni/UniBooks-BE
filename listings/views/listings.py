from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.core.cache import cache
from listings.models import Listing
from listings.serializers import ListingSerializer

from rest_framework.throttling import ScopedRateThrottle

from core.cache import HOME_RECENT_TTL, LISTING_CACHE_TTL, versioned_key


class ListingListCreateView(views.APIView):
    permission_classes = [AllowAny]

    def get_throttles(self):
        if self.request and self.request.method == 'POST':
            self.throttle_scope = 'listing_create'
        else:
            self.throttle_scope = 'search'
        return [ScopedRateThrottle()]

    def get(self, request):
        from core.i18n import resolve_language
        lang = resolve_language(request)

        school = request.query_params.get('school', '')
        seller_id = request.query_params.get('seller_id', '')
        page_param = request.query_params.get('page', '1')

        cache_key = versioned_key('listing_list', lang, school, seller_id, page_param)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)
        # select_related avoids per-row queries for the serializer's related fields
        listings = Listing.objects.filter(status='active').select_related(
                'book', 'seller', 'seller__school'
        ).order_by('-created_at')
        if school:
            listings = listings.filter(seller__school__name=school)
        if seller_id:
            listings = listings.filter(seller_id=seller_id)

        listings = listings[:4000]

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(listings, request)
        if page is not None:
            serializer = ListingSerializer(page, many=True, context={'request': request})
            response_data = paginator.get_paginated_response(serializer.data).data
            cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
            return Response(response_data)

        serializer = ListingSerializer(listings, many=True, context={'request': request})
        response_data = serializer.data
        cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
        return Response(response_data)

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": {"code": "auth.errNotLoggedIn"}}, status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_verified():
            return Response({"error": {"code": "acct.errUnverified"}}, status=status.HTTP_403_FORBIDDEN)

        serializer = ListingSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(seller=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response({"error": {"code": "sell.errValidation", "fields": serializer.errors}}, status=status.HTTP_400_BAD_REQUEST)


class RecentBooksView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from core.i18n import resolve_language
        lang = resolve_language(request)

        school = request.query_params.get('school', '')
        page_param = request.query_params.get('page', '1')

        limit_param = request.query_params.get('limit', '200')
        import urllib.parse
        safe_school = urllib.parse.quote(school)
        cache_key = f"recent_books_{lang}_{safe_school}_{page_param}_{limit_param}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        from catalog.models import Book
        from collections import Counter
        from django.db.models import Max, Q
        from rest_framework.pagination import PageNumberPagination

        # Both conditions go into a single filter() call on purpose. Chaining a
        # second .filter() over the same multi-valued relation lets Django
        # satisfy each one from a *different* listing row, so a book qualified
        # as long as it had some active listing anywhere and some listing at
        # this school — even if that school had none active. Those books then
        # found no rows in the per-book aggregation below and rendered with a
        # null price and zero sellers. search/views.py already builds its
        # filter this way.
        book_filter = Q(listings__status='active')
        if school:
            book_filter &= Q(listings__seller__school__name=school)
        books_qs = Book.objects.filter(book_filter)

        books_qs = books_qs.annotate(
            latest_listing=Max('listings__created_at')
        ).order_by('-latest_listing')

        paginator = PageNumberPagination()
        paginated_books = paginator.paginate_queryset(books_qs, request)
        if paginated_books is not None:
            # Slice only after pagination to maintain queryset integrity
            limit = int(limit_param) if limit_param.isdigit() else 200
            books_to_process = paginated_books[:limit]
        else:
            books_to_process = books_qs[:int(limit_param) if limit_param.isdigit() else 200]

        book_ids = [b.id for b in books_to_process]

        if not book_ids:
            return paginator.get_paginated_response([]) if paginated_books is not None else Response([])

        active_filter = {'book_id__in': book_ids, 'status': 'active'}
        if school:
            active_filter['seller__school__name'] = school

        all_book_listings = Listing.objects.filter(**active_filter).values('book_id', 'price', 'condition')

        book_stats = {}
        for lst in all_book_listings:
            b_id = lst['book_id']
            if b_id not in book_stats:
                book_stats[b_id] = {'prices': [], 'conditions': Counter()}
            book_stats[b_id]['prices'].append(lst['price'])
            book_stats[b_id]['conditions'][lst['condition']] += 1

        results = []
        for book in books_to_process:
            stats = book_stats.get(book.id, {'prices': [], 'conditions': Counter()})
            avg_price = sum(stats['prices']) / len(stats['prices']) if stats['prices'] else None

            results.append({
                'id': book.id,
                'isbn': book.isbn13,
                'title': book.title,
                'authors': book.authors,
                'cover_url': book.cover_url,
                'avg_price': round(avg_price) if avg_price is not None else None,
                'conditions': dict(stats['conditions'])
            })

        if paginated_books is not None:
            response_data = paginator.get_paginated_response(results).data
        else:
            response_data = results

        cache.set(cache_key, response_data, timeout=HOME_RECENT_TTL)
        return Response(response_data)


class ListingDetailView(views.APIView):
    permission_classes = [AllowAny]

    def get_object(self, request, pk, require_seller=True):
        try:
            listing = Listing.objects.select_related(
            'book', 'seller', 'seller__school'
            ).get(pk=pk)
            if require_seller and listing.seller != request.user:
                return None
            return listing
        except Listing.DoesNotExist:
            return None

    def get(self, request, pk):
        from core.i18n import resolve_language
        lang = resolve_language(request)

        cache_key = versioned_key(f'listing:{pk}', lang)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        listing = self.get_object(request, pk, require_seller=False)
        if not listing:
             return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ListingSerializer(listing, context={'request': request})
        response_data = serializer.data
        cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
        return Response(response_data)

    def patch(self, request, pk):
        listing = self.get_object(request, pk, require_seller=True)
        if not listing:
             return Response(status=status.HTTP_404_NOT_FOUND)

        # Handle manual book updates
        if listing.book.source == 'manual':
            book_title = request.data.get('book_title')
            book_authors = request.data.get('book_authors')
            isbn = request.data.get('isbn')
            updated_book = False
            if book_title is not None:
                listing.book.title = book_title
                updated_book = True
            if book_authors is not None:
                listing.book.authors = book_authors
                updated_book = True
            if isbn is not None:
                listing.book.isbn13 = isbn
                updated_book = True
            if updated_book:
                listing.book.save()

        serializer = ListingSerializer(listing, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
             # Cache invalidation (every language variant, the listing lists
             # and the book page) happens in the post_save signal — see
             # listings.models.invalidate_listing_caches.
             serializer.save()
             return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        listing = self.get_object(request, pk, require_seller=True)
        if not listing:
            return Response(status=status.HTTP_404_NOT_FOUND)
        book = listing.book
        listing.delete()  # post_delete signal invalidates the caches

        # If the book now has zero listings and zero subscriptions,
        # it’s an orphan — delete it immediately so the catalog
        # doesn’t accumulate dead entries.
        from django.db.models import Count
        from catalog.models import Book
        orphan = (
            Book.objects
            .filter(id=book.id)
            .annotate(
                listing_count=Count('listings'),
                subscription_count=Count('subscriptions'),
            )
            .filter(
                listing_count=0,
                subscription_count=0,
            )
        )
        orphan.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

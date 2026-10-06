from core.region import get_region
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from django.core.cache import cache
from listings.models import Listing
from listings.serializers import ListingSerializer, with_seller_stats
from listings.utils import delete_listing

from core.throttling import ScopedThrottle

from core.cache import HOME_RECENT_TTL, LISTING_CACHE_TTL, region_versioned_key
from core.permissions import IsVerifiedInRegion
from accounts.school_codes import school_filter_id, school_filter_scope


class ListingListCreateView(views.APIView):
    permission_classes = [IsAuthenticatedOrReadOnly, IsVerifiedInRegion]

    def get_target_region(self, request):
        if request.method == 'POST':
            return get_region(request)
        return None

    def get_throttles(self):
        if self.request and self.request.method == 'POST':
            self.throttle_scope = 'listing_create'
        else:
            self.throttle_scope = 'search'
        return [ScopedThrottle()]

    def get(self, request):
        from core.i18n import resolve_language
        lang = resolve_language(request)

        school = request.query_params.get('school', '')
        seller_id = request.query_params.get('seller_id', '')
        page_param = request.query_params.get('page', '1')

        region = get_region(request)
        # Keyed on the resolved id, not the raw parameter: "ntu", "NTU" and
        # the legacy full name are one school and share one cache entry.
        school_id = school_filter_id(region, school)
        cache_key = region_versioned_key(region, 'listing_list', lang, '' if school_id is None else school_id, seller_id, page_param)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)
        # select_related avoids per-row queries for the serializer's related fields
        listings = with_seller_stats(Listing.objects.filter(region=region, status='active').select_related(
                'book', 'seller', 'school'
        )).order_by('-created_at')
        if school_id is not None:
            listings = listings.filter(school_id=school_id)
        if seller_id:
            listings = listings.filter(seller_id=seller_id)

        listings = listings[:4000]

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(listings, request)
        # Cached and served to every visitor: seller-only fields stay out.
        public_context = {'request': request, 'strip_private_note': True}
        if page is not None:
            serializer = ListingSerializer(page, many=True, context=public_context)
            response_data = paginator.get_paginated_response(serializer.data).data
            cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
            return Response(response_data)

        serializer = ListingSerializer(listings, many=True, context=public_context)
        response_data = serializer.data
        cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)
        return Response(response_data)

    def post(self, request):
        region = get_region(request)

        serializer = ListingSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            verification = request.user.region_verifications.verified_in(region).first()
            school = verification.school if verification else None
            serializer.save(seller=request.user, region=region, currency=region.currency, school=school)
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
        # Normalise both before they reach the cache key: the raw strings are
        # attacker-chosen, so every distinct value would otherwise mint a new
        # cache entry (`?limit=201`, `?limit=202`, ...) — unbounded key growth
        # with zero hit rate. A page size is also a real cap, not a suggestion.
        limit = min(max(int(limit_param), 1), 200) if limit_param.isdigit() else 200
        page_param = page_param if page_param.isdigit() else '1'
        region = get_region(request)
        # The resolved id rather than the raw (attacker-chosen) string also
        # bounds the cache keys the same way `limit` is bounded above.
        school_id, city = school_filter_scope(region, school)
        cache_key = f"{region.code}_recent_books_{lang}_{'' if school_id is None else school_id}_{page_param}_{limit}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        from catalog.models import Book
        from collections import Counter
        from django.db.models import Max, Q
        from rest_framework.pagination import PageNumberPagination

        # Filter by region
        base_qs = Book.objects.filter(region=region)
        # Both conditions go into a single filter() call on purpose. Chaining a
        # second .filter() over the same multi-valued relation lets Django
        # satisfy each one from a *different* listing row, so a book qualified
        # as long as it had some active listing anywhere and some listing at
        # this school — even if that school had none active. Those books then
        # found no rows in the per-book aggregation below and rendered with a
        # null price and zero sellers. search/views.py already builds its
        # filter this way.
        book_filter = Q(listings__status='active')
        if school_id is not None:
            book_filter &= Q(listings__school_id=school_id)
        # Must build on `base_qs`, not a fresh Book.objects — starting over
        # here silently dropped the region filter and served every region's
        # books from this endpoint. The cache key is region-stamped, so the
        # leak survived a cache-key audit: only the query was wrong.
        books_qs = base_qs.filter(book_filter)

        # The chosen school has no books: show the rest of its city's rather
        # than an empty shelf, and say so (`scope`), so the page can tell the
        # visitor these are not from their school. Only when the city has
        # some — otherwise the school's own empty state is the honest answer.
        # Same single-filter() rule as above.
        scope = 'all' if school_id is None else 'school'
        if city is not None and not books_qs.exists():
            city_qs = base_qs.filter(listings__status='active', listings__school__city_id=city.id)
            if city_qs.exists():
                books_qs = city_qs
                scope = 'city'
        scope_fields = {'scope': scope, 'city': city.code if city else None}

        books_qs = books_qs.annotate(
            latest_listing=Max('listings__created_at')
        ).order_by('-latest_listing')

        paginator = PageNumberPagination()
        paginated_books = paginator.paginate_queryset(books_qs, request)
        if paginated_books is not None:
            # Slice only after pagination to maintain queryset integrity
            books_to_process = paginated_books[:limit]
        else:
            books_to_process = books_qs[:limit]

        book_ids = [b.id for b in books_to_process]

        if not book_ids:
            if paginated_books is None:
                return Response([])
            return Response({**paginator.get_paginated_response([]).data, **scope_fields})

        # `region` here too: book_ids are already region-scoped, but the price
        # and condition stats aggregate listings, and a book carried by both
        # regions would otherwise mix another region's prices into this one.
        active_filter = {'book_id__in': book_ids, 'status': 'active', 'region': region}
        if scope == 'city':
            active_filter['school__city_id'] = city.id
        elif school_id is not None:
            active_filter['school_id'] = school_id

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
            # The tile quotes what the cheapest copy costs. An average is a
            # price nobody is asking: NT$10,099 and NT$100 listed a book at
            # NT$5,100. avg_price stays for frontends still reading it.
            min_price = min(stats['prices']) if stats['prices'] else None
            # Lets the tile say "From" only when the copies' prices differ.
            max_price = max(stats['prices']) if stats['prices'] else None

            results.append({
                'id': book.id,
                'isbn': book.isbn13,
                'title': book.title,
                'authors': book.authors,
                'cover_url': book.cover_url,
                'avg_price': round(avg_price) if avg_price is not None else None,
                'min_price': min_price,
                'max_price': max_price,
                'conditions': dict(stats['conditions'])
            })

        if paginated_books is not None:
            response_data = {**paginator.get_paginated_response(results).data, **scope_fields}
        else:
            response_data = results

        cache.set(cache_key, response_data, timeout=HOME_RECENT_TTL)
        return Response(response_data)


class ListingDetailView(views.APIView):
    permission_classes = [AllowAny]

    def get_object(self, request, pk, require_seller=True):
        try:
            listing = with_seller_stats(Listing.objects.select_related(
            'book', 'seller', 'school'
            )).get(pk=pk)
            if require_seller and listing.seller != request.user:
                return None
            return listing
        except Listing.DoesNotExist:
            return None

    def get(self, request, pk):
        from core.i18n import resolve_language
        lang = resolve_language(request)

        region = get_region(request)
        cache_key = region_versioned_key(region, f'listing:{pk}', lang)
        response_data = cache.get(cache_key)
        if response_data is None:
            listing = self.get_object(request, pk, require_seller=False)
            if not listing:
                return Response(status=status.HTTP_404_NOT_FOUND)
            # The cached body is shared by every viewer, so it is serialized
            # without the seller-only note regardless of who triggered the
            # rebuild — see ListingSerializer.to_representation.
            serializer = ListingSerializer(listing, context={'request': request, 'strip_private_note': True})
            response_data = serializer.data
            cache.set(cache_key, response_data, timeout=LISTING_CACHE_TTL)

        # The seller gets their note back, read fresh and never cached.
        if request.user.is_authenticated and response_data.get('seller') == request.user.id:
            note = Listing.objects.filter(pk=pk).values_list('private_note', flat=True).first()
            response_data = {**response_data, 'private_note': note or ''}
        return Response(response_data)

    def patch(self, request, pk):
        listing = self.get_object(request, pk, require_seller=True)
        if not listing:
             return Response(status=status.HTTP_404_NOT_FOUND)
        
        if listing.admin_locked:
            return Response({"error": {"code": "listing.errAdminLocked", "message": "This listing has been locked by UniBooks and cannot be modified."}}, status=status.HTTP_403_FORBIDDEN)

        # Handle manual book updates
        book_fields_sent = any(k in request.data for k in ('book_title', 'book_authors', 'isbn'))
        if listing.book.source == 'manual' and book_fields_sent:
            book = listing.book
            # A Book row is shared by every listing of that title. Letting one
            # seller rewrite it would silently retitle other sellers' listings
            # (and their buyers' orders), so edits are only allowed while this
            # seller is the book's sole lister.
            if book.listings.exclude(seller=request.user).exists():
                return Response({"error": {"code": "listing.errBookShared"}}, status=status.HTTP_403_FORBIDDEN)

            update_fields = []
            before = (book.title, book.authors, book.isbn13)
            book_title = request.data.get('book_title')
            if book_title is not None:
                if not isinstance(book_title, str) or not book_title.strip():
                    return Response({"error": {"code": "sell.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
                book.title = book_title.strip()[:255]
                update_fields.append('title')
            book_authors = request.data.get('book_authors')
            if book_authors is not None:
                if not isinstance(book_authors, str):
                    return Response({"error": {"code": "sell.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
                book.authors = book_authors.strip()[:512]
                update_fields.append('authors')
            isbn = request.data.get('isbn')
            if isbn is not None:
                if isbn == '':
                    book.isbn13 = None
                else:
                    from catalog.models import Book
                    from catalog.services import validate_book_isbn
                    valid_isbn = validate_book_isbn(isbn)
                    if not valid_isbn:
                        return Response({"error": {"code": "listing.errInvalidIsbn"}}, status=status.HTTP_400_BAD_REQUEST)
                    # isbn13 is unique within a region: answer 400 instead of
                    # letting the IntegrityError surface as a 500.
                    if Book.objects.filter(region_id=book.region_id, isbn13=valid_isbn).exclude(pk=book.pk).exists():
                        return Response({"error": {"code": "listing.errIsbnTaken"}}, status=status.HTTP_400_BAD_REQUEST)
                    book.isbn13 = valid_isbn
                update_fields.append('isbn13')
            if update_fields:
                if (book.title, book.authors, book.isbn13) != before:
                    # What the admin confirmed no catalogue has was the old
                    # record; the seller's new one goes back in the review queue.
                    book.reviewed_at = None
                    book.reviewed_by = None
                    update_fields += ['reviewed_at', 'reviewed_by']
                book.save(update_fields=update_fields)

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
        if listing.admin_locked:
            return Response({"error": {"code": "listing.errAdminLocked"}}, status=status.HTTP_403_FORBIDDEN)
        # Finished orders, the chat and any report outlive the listing on
        # their snapshot (listings/snapshot.py). An open order still needs it,
        # so the seller has to finish or cancel those first.
        from orders.models import ACTIVE_ORDER_STATUSES
        if listing.orders.filter(status__in=ACTIVE_ORDER_STATUSES).exists():
            return Response({"error": {"code": "listing.errHasActiveOrders"}}, status=status.HTTP_409_CONFLICT)
        delete_listing(listing)
        return Response(status=status.HTTP_204_NO_CONTENT)

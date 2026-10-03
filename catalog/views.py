from django.db.models import Max, Min
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from catalog.models import Book
from catalog.serializers import BookSerializer
from catalog.services import (
    get_google_books_by_isbn, get_open_library_book_by_isbn,
    get_isbnnet_book_by_isbn,
    clean_publisher,
    describe_source,
)
from catalog.services.engines import (
    ISBN_FALLBACK_ORDER, SOURCE_BY_ENGINE,
    engine_order, lookup_in_order, region_search_engines,
)
from listings.serializers import ListingSerializer, with_seller_stats
from django.core.cache import cache

from core.cache import BOOK_DETAIL_CACHE_TTL, versioned_key, region_versioned_key
from core.region import get_region
from core.authentication import OptionalJWTAuthentication


class BookDetailView(views.APIView):
    permission_classes = [AllowAny]
    authentication_classes = [OptionalJWTAuthentication]

    def get(self, request):
        isbn = request.query_params.get('isbn')
        book_id = request.query_params.get('id')
        page_param = request.query_params.get('page', '1')
        if not isbn and not book_id:
            return Response({"error": "isbn or id is required"}, status=status.HTTP_400_BAD_REQUEST)

        valid_isbn = None
        if isbn:
            from catalog.services import clean_and_validate_isbn
            valid_isbn = clean_and_validate_isbn(isbn)

        # One generation for all book pages rather than one per book: this
        # endpoint is usually addressed by ISBN (see the frontend's goToBook),
        # and the post_save signal only cheaply knows the listing's book_id,
        # so a per-book generation would silently miss the common path. Any
        # listing write therefore invalidates every book page — acceptable
        # while the catalog is small (each page costs one rebuild), and worth
        # revisiting for a per-book generation if book traffic grows.
        #
        # The `page` component is why this needs a generation at all: it made
        # the key space unbounded, so these entries could never be invalidated
        # and a sold or deleted listing stayed advertised until the TTL lapsed.
        from core.i18n import resolve_language
        lang = resolve_language(request)
        region = get_region(request)
        # Same rules as BookSearchView: a named engine the region allows is
        # used on its own, anything else gets the region's fallback order.
        allowed_engines = region_search_engines(region)
        requested_engine = request.query_params.get('engine')
        engine = requested_engine if requested_engine in allowed_engines else None
        cache_key = region_versioned_key(region, "book_detail", lang, book_id or '', valid_isbn or '', page_param, engine or 'auto')
        data = cache.get(cache_key)

        book = None
        if not data:
            try:
                if valid_isbn:
                    book = Book.objects.filter(region=region, isbn13=valid_isbn).first()
                if not book:
                    # Serve dynamically. A named engine is asked first and on
                    # its own: the link was built from a search result that
                    # engine produced (see the frontend's bookLinkParams), and
                    # another engine's data could show a different cover or
                    # title than the result just clicked. But a page that
                    # says the book does not exist is worse than that, and a
                    # catalogue's ISBN lookup can miss a book its own search
                    # returned — so when the named engine has nothing, the
                    # rest of the region's catalogues are tried in order.
                    # Without an engine — a shared or typed-in ISBN link —
                    # that order is all there is.
                    gb_data = None
                    if valid_isbn:
                        lookups = {
                            'googlebooks': get_google_books_by_isbn,
                            'isbnnet': get_isbnnet_book_by_isbn,
                            'openlibrary': get_open_library_book_by_isbn,
                        }
                        order = engine_order(engine, allowed_engines, ISBN_FALLBACK_ORDER)
                        gb_data, engine_used, meta, _ = lookup_in_order(valid_isbn, order, lookups, allowed_engines)
                        if not gb_data and engine:
                            tried = {attempt['engine'] for attempt in meta['attempts']}
                            rest = [e for e in engine_order(None, allowed_engines, ISBN_FALLBACK_ORDER) if e not in tried]
                            if rest:
                                gb_data, engine_used, meta, _ = lookup_in_order(valid_isbn, rest, lookups, allowed_engines)
                    if gb_data:
                        return Response({
                            'id': '',
                            'isbn13': valid_isbn,
                            'title': gb_data['title'],
                            'authors': gb_data['authors'],
                            # Cleaned here as well as at import: lookups are
                            # cached for 30 days, so entries stored before the
                            # import fix still carry the quotes.
                            'publisher': clean_publisher(gb_data.get('publisher', '')),
                            'published_date': gb_data.get('published_date', ''),
                            'cover_url': gb_data.get('cover_url', ''),
                            'source': SOURCE_BY_ENGINE[engine_used],
                            'debug_source': describe_source(engine_used, meta.get('cache_hit', False)),
                            'listings': {
                                'count': 0,
                                'next': None,
                                'previous': None,
                                'results': []
                            },
                            'price_stats': {'count': 0, 'min': None, 'max': None},
                            'waiting_count': 0,
                            'is_subscribed': False,
                            'subscription_id': None
                        }, status=status.HTTP_200_OK)
                    if not book_id:
                        return Response({"error": "Book not found"}, status=status.HTTP_404_NOT_FOUND)
            
                if book_id:
                    book = Book.objects.get(region=region, id=book_id)
                elif not book:
                    return Response({"error": "Book not found"}, status=status.HTTP_404_NOT_FOUND)

                serializer = BookSerializer(book)
                data = serializer.data
                # This body is cached and shared by every viewer of the book
                # page — seller-only fields must not be serialized into it.
                public_context = {'request': request, 'strip_private_note': True}
                active_listings = with_seller_stats(book.listings.filter(region=region, status='active').select_related(
                    'book', 'seller', 'school'
                )).order_by('-created_at')
                
                from rest_framework.pagination import PageNumberPagination
                paginator = PageNumberPagination()
                page = paginator.paginate_queryset(active_listings, request)
                if page is not None:
                    data['listings'] = {
                        'count': paginator.page.paginator.count,
                        'next': paginator.get_next_link(),
                        'previous': paginator.get_previous_link(),
                        'results': ListingSerializer(page, many=True, context=public_context).data
                    }
                else:
                    data['listings'] = {
                        'count': active_listings.count(),
                        'next': None,
                        'previous': None,
                        'results': ListingSerializer(active_listings, many=True, context=public_context).data
                    }
                # The price range across every active copy, not just the page
                # of listings above: that page holds the 20 newest, so a
                # cheaper older copy would be missing from a range the sell
                # form derived from it. Seller-agnostic because this body is
                # cached for everyone — a seller's own copies are included.
                price_range = active_listings.aggregate(min=Min('price'), max=Max('price'))
                data['price_stats'] = {
                    'count': data['listings']['count'],
                    'min': price_range['min'],
                    'max': price_range['max'],
                }
                data['waiting_count'] = book.subscriptions.count()
                cache.set(cache_key, data, timeout=BOOK_DETAIL_CACHE_TTL)
            
            except (Book.DoesNotExist, ValueError):
                return Response({"error": {"code": "not_found", "message": "Book not found"}}, status=status.HTTP_404_NOT_FOUND)

        # Copy data so we don't mutate the cached object directly
        response_data = dict(data)
        response_data['is_subscribed'] = False
        response_data['subscription_id'] = None

        if request.user.is_authenticated and response_data.get('id'):
            from subscriptions.models import Subscription
            sub = Subscription.objects.filter(region=region, user=request.user, book_id=response_data['id']).first()
            if sub:
                response_data['is_subscribed'] = True
                response_data['subscription_id'] = sub.id

        school_param = request.query_params.get('school')
        if school_param and response_data.get('id'):
            from listings.models import Listing
            from accounts.school_codes import school_filter_id
            response_data['local_listings_count'] = Listing.objects.filter(
                region=region,
                book_id=response_data['id'],
                status='active',
                school_id=school_filter_id(region, school_param),
            ).count()
        else:
            response_data['local_listings_count'] = None

        return Response(response_data)


class ManualBookCreateView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data.copy()
        if 'cover_url' in data:
            # The cover rides along from a search result, so an unusable one
            # is left out rather than failing the seller's listing.
            from catalog.services import clean_cover_url
            data['cover_url'] = clean_cover_url(data['cover_url']) or ''
        isbn13 = data.get('isbn13')
        from core.region import get_region
        region = get_region(request)
        if isbn13 == '':
            data['isbn13'] = None
        elif isbn13:
            from catalog.services import validate_book_isbn
            isbn13 = validate_book_isbn(isbn13)
            if not isbn13:
                return Response({"error": {"code": "listing.errInvalidIsbn"}}, status=status.HTTP_400_BAD_REQUEST)
            data['isbn13'] = isbn13
            existing_book = Book.objects.filter(region=region, isbn13=isbn13).first()
            if existing_book:
                updated = False
                if not existing_book.cover_url and data.get('cover_url'):
                    existing_book.cover_url = data['cover_url']
                    updated = True
                if not existing_book.publisher and data.get('publisher'):
                    existing_book.publisher = clean_publisher(data['publisher'])
                    updated = True
                if not existing_book.published_date and data.get('published_date'):
                    existing_book.published_date = data['published_date']
                    updated = True
                
                if updated:
                    existing_book.save()

                serializer = BookSerializer(existing_book)
                return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = BookSerializer(data=data)
        if serializer.is_valid():
            source = data.get('source', 'manual')
            valid_sources = dict(Book.SOURCE_CHOICES).keys()
            if source not in valid_sources:
                source = 'manual'
            serializer.save(source=source, region=region)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

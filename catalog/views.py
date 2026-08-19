from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from catalog.models import Book
from catalog.serializers import BookSerializer
from catalog.services import (
    get_google_books_by_isbn, get_open_library_book_by_isbn,
    GoogleBooksRateLimited, describe_source,
)
from listings.serializers import ListingSerializer
from django.core.cache import cache

from core.cache import BOOK_DETAIL_CACHE_TTL, versioned_key
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
        cache_key = versioned_key("book_detail", lang, book_id or '', valid_isbn or '', page_param)
        data = cache.get(cache_key)

        book = None
        if not data:
            try:
                if valid_isbn:
                    book = Book.objects.filter(isbn13=valid_isbn).first()
                if not book:
                    # Serve dynamically from Google Books, falling back to Open
                    # Library if Google rate-limits us (429).
                    source = 'google_api'
                    engine_used = 'google'
                    meta = {}
                    try:
                        gb_data = get_google_books_by_isbn(valid_isbn, _meta=meta)
                    except GoogleBooksRateLimited:
                        engine_used = 'openlibrary'
                        meta = {}
                        gb_data = get_open_library_book_by_isbn(valid_isbn, _meta=meta)
                        source = 'openlibrary_api'
                    if gb_data:
                        return Response({
                            'id': '',
                            'isbn13': valid_isbn,
                            'title': gb_data['title'],
                            'authors': gb_data['authors'],
                            'publisher': gb_data.get('publisher', ''),
                            'published_date': gb_data.get('published_date', ''),
                            'cover_url': gb_data.get('cover_url', ''),
                            'source': source,
                            'debug_source': describe_source(engine_used, meta.get('cache_hit', False)),
                            'listings': {
                                'count': 0,
                                'next': None,
                                'previous': None,
                                'results': []
                            },
                            'waiting_count': 0,
                            'is_subscribed': False,
                            'subscription_id': None
                        }, status=status.HTTP_200_OK)
                    if not book_id:
                        return Response({"error": "Book not found"}, status=status.HTTP_404_NOT_FOUND)
            
                if book_id:
                    book = Book.objects.get(id=book_id)
                elif not book:
                    return Response({"error": "Book not found"}, status=status.HTTP_404_NOT_FOUND)

                serializer = BookSerializer(book)
                data = serializer.data
                active_listings = book.listings.filter(status='active').select_related(
                    'book', 'seller', 'school'
                ).order_by('-created_at')
                
                from rest_framework.pagination import PageNumberPagination
                paginator = PageNumberPagination()
                page = paginator.paginate_queryset(active_listings, request)
                if page is not None:
                    data['listings'] = {
                        'count': paginator.page.paginator.count,
                        'next': paginator.get_next_link(),
                        'previous': paginator.get_previous_link(),
                        'results': ListingSerializer(page, many=True, context={'request': request}).data
                    }
                else:
                    data['listings'] = {
                        'count': active_listings.count(),
                        'next': None,
                        'previous': None,
                        'results': ListingSerializer(active_listings, many=True, context={'request': request}).data
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
            sub = Subscription.objects.filter(user=request.user, book_id=response_data['id']).first()
            if sub:
                response_data['is_subscribed'] = True
                response_data['subscription_id'] = sub.id

        school_param = request.query_params.get('school')
        if school_param and response_data.get('id'):
            from listings.models import Listing
            response_data['local_listings_count'] = Listing.objects.filter(
                book_id=response_data['id'],
                status='active',
                seller__school__name=school_param
            ).count()
        else:
            response_data['local_listings_count'] = None

        return Response(response_data)

class ManualBookCreateView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data.copy()
        isbn13 = data.get('isbn13')
        if isbn13 == '':
            data['isbn13'] = None
        elif isbn13:
            existing_book = Book.objects.filter(isbn13=isbn13).first()
            if existing_book:
                updated = False
                if not existing_book.cover_url and data.get('cover_url'):
                    existing_book.cover_url = data['cover_url']
                    updated = True
                if not existing_book.publisher and data.get('publisher'):
                    existing_book.publisher = data['publisher']
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
            serializer.save(source=source)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

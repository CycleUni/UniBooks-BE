from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from core.authentication import OptionalJWTAuthentication
from catalog.services import (
    search_google_books, get_google_books_by_isbn,
    search_open_library_books, get_open_library_book_by_isbn,
    GoogleBooksRateLimited, describe_source,
)
from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription
from django.db.models import Count, Min, Q

VALID_SEARCH_ENGINES = {'googlebooks', 'openlibrary'}


class BookSearchView(views.APIView):
    authentication_classes = [OptionalJWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'search'

    def get(self, request):
        query = request.GET.get('q', '')
        category = request.GET.get('category', '')
        course = request.GET.get('course', '')
        school = request.GET.get('school', '')
        engine = request.GET.get('engine', 'googlebooks')
        if engine not in VALID_SEARCH_ENGINES:
            engine = 'googlebooks'

        if not query and not category and not course:
            return Response([])

        # True only when a Google attempt was actually made and rate-limited
        # (429), forcing the automatic Open Library fallback. Surfaced to the
        # frontend so it can annotate the engine selector (e.g. "Google
        # temporarily unavailable") without guessing from per-item `source`.
        google_unavailable = False

        if category or (course and not query):
            base_filter = Q(listings__status='active')
            if category:
                base_filter &= Q(listings__category__slug=category)
            if course:
                base_filter &= Q(listings__course_name=course)
            if school:
                base_filter &= Q(listings__seller__school__name=school)
            books = Book.objects.filter(base_filter).distinct()
            results = []
            for book in books:
                results.append({
                    'id': str(book.id),
                    'isbn': book.isbn13,
                    'title': book.title,
                    'authors': book.authors,
                    'publisher': book.publisher,
                    'published_date': book.published_date,
                    'cover_url': book.cover_url,
                    'source': book.source,
                })
        else:
            query_stripped = query.strip()
            is_isbn = query_stripped.isdigit() and len(query_stripped) in (10, 13)
            
            if is_isbn:
                engine_used = engine
                meta = {}
                if engine == 'openlibrary':
                    gb_book = get_open_library_book_by_isbn(query_stripped, _meta=meta)
                else:
                    try:
                        gb_book = get_google_books_by_isbn(query_stripped, _meta=meta)
                    except GoogleBooksRateLimited:
                        engine_used = 'openlibrary'
                        google_unavailable = True
                        meta = {}
                        gb_book = get_open_library_book_by_isbn(query_stripped, _meta=meta)
                if gb_book:
                    gb_book['source'] = 'google_api' if engine_used == 'googlebooks' else 'openlibrary_api'
                    gb_book['debug_source'] = describe_source(engine_used, meta.get('cache_hit', False))
                gb_results = [gb_book] if gb_book else []
                local_books = Book.objects.filter(isbn13=query_stripped)

                # The dedicated isbn: lookup occasionally misses a book that
                # Google/Open Library do have indexed under this exact ISBN
                # (observed for at least one Taiwanese publisher title) even
                # though their plain keyword search finds it. Only spend the
                # extra call when the strict lookup came up empty, and only
                # keep results whose own ISBN matches what was searched, so
                # this stays a precise ISBN lookup rather than a fuzzy one.
                if not gb_book and not local_books.exists():
                    try:
                        if engine_used == 'openlibrary':
                            fallback_results = search_open_library_books(query_stripped, _meta={})
                        else:
                            fallback_results = search_google_books(query_stripped, _meta={})
                    except GoogleBooksRateLimited:
                        fallback_results = []
                    fallback_results = [item for item in fallback_results if item.get('isbn') == query_stripped]
                    for item in fallback_results:
                        item['source'] = 'google_api' if engine_used == 'googlebooks' else 'openlibrary_api'
                    gb_results = fallback_results
            else:
                engine_used = engine
                meta = {}
                if engine == 'openlibrary':
                    gb_results = search_open_library_books(query, _meta=meta)
                else:
                    try:
                        gb_results = search_google_books(query, _meta=meta)
                    except GoogleBooksRateLimited:
                        engine_used = 'openlibrary'
                        google_unavailable = True
                        meta = {}
                        gb_results = search_open_library_books(query, _meta=meta)
                debug_source = describe_source(engine_used, meta.get('cache_hit', False))
                for gb_book in gb_results:
                    gb_book['source'] = 'google_api' if engine_used == 'googlebooks' else 'openlibrary_api'
                    gb_book['debug_source'] = debug_source
                listing_text_match = Q(listings__status='active') & (
                    Q(listings__course_name__icontains=query) |
                    Q(listings__professor_name__icontains=query)
                )
                if school:
                    listing_text_match &= Q(listings__seller__school__name=school)

                local_books = Book.objects.filter(
                    Q(title__icontains=query) | 
                    Q(authors__icontains=query) | 
                    Q(isbn13__icontains=query) |
                    listing_text_match
                )
                if course:
                    course_filter = Q(listings__course_name=course, listings__status='active')
                    if school:
                        course_filter &= Q(listings__seller__school__name=school)
                    local_books = local_books.filter(course_filter)
                
                # Limit local books to 100 to prevent memory explosion when merging
                # with Google Books API results in Python.
                local_books = local_books.distinct()[:100]
            
            local_results = []
            for book in local_books:
                local_results.append({
                    'id': str(book.id),
                    'isbn': book.isbn13,
                    'title': book.title,
                    'authors': book.authors,
                    'publisher': book.publisher,
                    'published_date': book.published_date,
                    'cover_url': book.cover_url,
                    'source': book.source,
                })
                
            seen_isbns = set()
            results = []
            for item in local_results + gb_results:
                isbn = item.get('isbn')
                local_id = item.get('id')
                if isbn and isbn not in seen_isbns:
                    seen_isbns.add(isbn)
                    results.append(item)
                elif not isbn and local_id:
                    results.append(item)

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginated_results = paginator.paginate_queryset(results, request)
        results_to_process = paginated_results if paginated_results is not None else results

        # Enrich Google Books results with local data (activeListings, minPrice,
        # waitlistCount, id) in a fixed number of queries instead of per-item lookups.
        isbns = [item['isbn'] for item in results_to_process if item.get('isbn')]
        ids = [item['id'] for item in results_to_process if item.get('id')]
        books_by_isbn = {}
        books_by_id = {}
        if isbns or ids:
            global_active_filter = Q(listings__status='active')
            local_active_filter = Q(listings__status='active')
            if school:
                local_active_filter &= Q(listings__seller__school__name=school)

            books = Book.objects.filter(Q(isbn13__in=isbns) | Q(id__in=ids)).annotate(
                global_active_listings_count=Count(
                    'listings', filter=global_active_filter, distinct=True
                ),
                local_active_listings_count=Count(
                    'listings', filter=local_active_filter, distinct=True
                ),
                min_active_price=Min(
                    'listings__price', filter=global_active_filter
                ),
                waitlist_count=Count('subscriptions', distinct=True),
            )
            books_by_isbn = {book.isbn13: book for book in books if book.isbn13}
            books_by_id = {str(book.id): book for book in books}

        # Distinct active-listing conditions per book, for the frontend condition filter
        conditions_by_book_id = {}
        if books_by_id:
            condition_filter = {'book_id__in': list(books_by_id.keys()), 'status': 'active'}
            if school:
                condition_filter['seller__school__name'] = school
            condition_rows = Listing.objects.filter(**condition_filter).values_list('book_id', 'condition').distinct()
            for book_id, condition in condition_rows:
                conditions_by_book_id.setdefault(book_id, []).append(condition)

        subscription_by_book_id = {}
        if request.user.is_authenticated and books_by_isbn:
            subs = Subscription.objects.filter(
                user=request.user,
                book_id__in=[book.id for book in books_by_isbn.values()],
            )
            subscription_by_book_id = {sub.book_id: sub.id for sub in subs}

        enhanced_results = []
        for item in results_to_process:
            isbn = item.get('isbn')
            local_id = item.get('id')
            book = books_by_id.get(local_id) or (books_by_isbn.get(isbn) if isbn else None)
            subscription_id = subscription_by_book_id.get(book.id) if book else None

            enhanced_results.append({
                # Empty when the book does not exist locally yet; frontend must handle it
                'id': str(book.id) if book else '',
                'title': item.get('title', ''),
                'author': item.get('authors', ''),  # frontend expects `author`, not `authors`
                'isbn': isbn or '',
                'coverUrl': item.get('cover_url', ''),
                'publisher': item.get('publisher', ''),
                'published_date': item.get('published_date', ''),
                'source': item.get('source', 'manual'),
                # Dev-only monitoring field (not shown in the UI): which
                # engine actually served this item, and whether it was a
                # cache hit. Absent for locally-stored books.
                'debug_source': item.get('debug_source'),
                'activeListings': book.global_active_listings_count if book else 0,
                'localActiveListings': book.local_active_listings_count if book else 0,
                'minPrice': (book.min_active_price or 0) if book else 0,
                'waitlistCount': book.waitlist_count if book else 0,
                'conditions': conditions_by_book_id.get(book.id, []) if book else [],
                'is_subscribed': subscription_id is not None,
                'subscription_id': subscription_id,
            })

        if paginated_results is not None:
            response = paginator.get_paginated_response(enhanced_results)
            response.data['google_unavailable'] = google_unavailable
            return response
        return Response(enhanced_results)

class CourseListView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'search'

    @method_decorator(cache_page(60 * 60))
    def get(self, request):
        school = request.GET.get('school', '')
        category = request.GET.get('category', '')
        
        courses = Listing.objects.filter(status='active').exclude(course_name__exact='')
        if school:
            courses = courses.filter(seller__school__name=school)
        if category:
            courses = courses.filter(category__slug=category)
        
        course_counts = courses.values('course_name').annotate(
            count=Count('id')
        ).order_by('-count', 'course_name')[:20]
        
        results = [item['course_name'] for item in course_counts]
        return Response(results)

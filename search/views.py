from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from django.core.cache import cache
from core.authentication import OptionalJWTAuthentication
from core.cache import LISTING_CACHE_TTL, region_versioned_key
from catalog.services import (
    search_google_books, get_google_books_by_isbn,
    search_open_library_books, get_open_library_book_by_isbn,
    get_isbnnet_book_by_isbn,
    GoogleBooksRateLimited, describe_source,
)
from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription
from django.db.models import Count, Min, Q
from core.region import get_region

VALID_SEARCH_ENGINES = {'googlebooks', 'openlibrary', 'isbnnet'}

# Browsing by category or course reads Book rows straight out of the local
# catalogue, and the whole matching set used to be pulled into Python before
# paginating. The cap keeps that bounded; the response says when it bit
# (results_truncated) so the client can tell the user to narrow the filters
# rather than just running out of pages.
LOCAL_BROWSE_LIMIT = 200


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
        region = get_region(request)
        
        condition_param = request.GET.get('condition')
        if condition_param == 'none':
            allowed_conditions = set()
        elif condition_param:
            allowed_conditions = set(condition_param.split(','))
        else:
            allowed_conditions = None

        try:
            p_min_val = request.GET.get('price_min')
            p_min = int(p_min_val) if p_min_val and p_min_val.isdigit() else None
        except Exception:
            p_min = None
            
        try:
            p_max_val = request.GET.get('price_max')
            p_max = int(p_max_val) if p_max_val and p_max_val.isdigit() else None
        except Exception:
            p_max = None
            
        filter_price = (p_min is not None or p_max is not None)
        in_stock_required = (request.GET.get('in_stock') == '1')

        explicit_engine = request.GET.get('engine')
        engine = explicit_engine if explicit_engine in VALID_SEARCH_ENGINES else None
        if engine and engine not in (region.search_engines or ['googlebooks']):
            engine = None

        if not query and not category and not course:
            return Response([])

        google_unavailable = False
        # True when the browse branch stopped at LOCAL_BROWSE_LIMIT and there
        # are matches the caller can never page to. Saying so lets the client
        # tell the user to narrow the filters instead of just ending the list
        # a page early with no explanation.
        results_truncated = False

        if category or (course and not query):
            base_filter = Q(listings__status='active')
            if category:
                base_filter &= Q(listings__category__slug=category)
            if course:
                base_filter &= Q(listings__course_name=course)
            if school:
                base_filter &= Q(listings__school__name=school)
            # Bounded and ordered: this used to pull every matching Book into
            # Python before paginating, in whatever order the database felt
            # like, so page 2 could repeat page 1.
            books = list(
                Book.objects.filter(base_filter, region=region)
                .distinct()
                .order_by('-created_at')[:LOCAL_BROWSE_LIMIT]
            )
            results_truncated = len(books) == LOCAL_BROWSE_LIMIT
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
                meta = {}
                gb_book = None
                
                if engine:
                    engine_used = engine
                    if engine == 'openlibrary':
                        gb_book = get_open_library_book_by_isbn(query_stripped, _meta=meta)
                    elif engine == 'isbnnet':
                        gb_book = get_isbnnet_book_by_isbn(query_stripped, _meta=meta)
                    else:
                        try:
                            gb_book = get_google_books_by_isbn(query_stripped, _meta=meta)
                        except GoogleBooksRateLimited:
                            engine_used = 'openlibrary'
                            google_unavailable = True
                            meta = {}
                            gb_book = get_open_library_book_by_isbn(query_stripped, _meta=meta)
                else:
                    try:
                        gb_book = get_google_books_by_isbn(query_stripped, _meta=meta)
                        engine_used = 'googlebooks'
                    except GoogleBooksRateLimited:
                        google_unavailable = True
                    
                    if not gb_book:
                        meta = {}
                        gb_book = get_isbnnet_book_by_isbn(query_stripped, _meta=meta)
                        engine_used = 'isbnnet'
                        
                    if not gb_book:
                        meta = {}
                        gb_book = get_open_library_book_by_isbn(query_stripped, _meta=meta)
                        engine_used = 'openlibrary'

                if gb_book:
                    if not gb_book.get('cover_url') and gb_book.get('isbn'):
                        gb_book['cover_url'] = f"https://covers.openlibrary.org/b/isbn/{gb_book['isbn']}-L.jpg"
                    if engine_used == 'isbnnet':
                        gb_book['source'] = 'isbnnet_api'
                    elif engine_used == 'openlibrary':
                        gb_book['source'] = 'openlibrary_api'
                    else:
                        gb_book['source'] = 'google_api'
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
                        if engine == 'openlibrary':
                            fallback_results = search_open_library_books(query_stripped, _meta={})
                            fallback_engine = 'openlibrary'
                        else:
                            fallback_results = search_google_books(query_stripped, _meta={})
                            fallback_engine = 'googlebooks'
                    except GoogleBooksRateLimited:
                        fallback_results = []
                        fallback_engine = None
                    
                    if not fallback_results and not engine:
                        fallback_results = search_open_library_books(query_stripped, _meta={})
                        fallback_engine = 'openlibrary'
                        
                    fallback_results = [item for item in fallback_results if item.get('isbn') == query_stripped]
                    for item in fallback_results:
                        if not item.get('cover_url') and item.get('isbn'):
                            item['cover_url'] = f"https://covers.openlibrary.org/b/isbn/{item['isbn']}-L.jpg"
                        if fallback_engine == 'isbnnet':
                            item['source'] = 'isbnnet_api'
                        elif fallback_engine == 'openlibrary':
                            item['source'] = 'openlibrary_api'
                        else:
                            item['source'] = 'google_api'
                    gb_results = fallback_results
            else:
                meta = {}
                gb_results = []
                if engine:
                    engine_used = engine
                    if engine_used == 'isbnnet':
                        engine_used = 'googlebooks'

                    if engine_used == 'openlibrary':
                        gb_results = search_open_library_books(query, _meta=meta)
                    else:
                        try:
                            gb_results = search_google_books(query, _meta=meta)
                        except GoogleBooksRateLimited:
                            engine_used = 'openlibrary'
                            google_unavailable = True
                            meta = {}
                            gb_results = search_open_library_books(query, _meta=meta)
                else:
                    try:
                        gb_results = search_google_books(query, _meta=meta)
                        engine_used = 'googlebooks'
                    except GoogleBooksRateLimited:
                        google_unavailable = True
                        
                    if not gb_results:
                        meta = {}
                        gb_results = search_open_library_books(query, _meta=meta)
                        engine_used = 'openlibrary'

                debug_source = describe_source(engine_used, meta.get('cache_hit', False))
                for gb_book in gb_results:
                    if not gb_book.get('cover_url') and gb_book.get('isbn'):
                        gb_book['cover_url'] = f"https://covers.openlibrary.org/b/isbn/{gb_book['isbn']}-L.jpg"
                    gb_book['source'] = 'google_api' if engine_used == 'googlebooks' else 'openlibrary_api'
                    gb_book['debug_source'] = debug_source
                listing_text_match = Q(listings__status='active') & (
                    Q(listings__course_name__icontains=query) |
                    Q(listings__professor_name__icontains=query)
                )
                if school:
                    listing_text_match &= Q(listings__school__name=school)

                local_books = Book.objects.filter(
                    Q(title__icontains=query) | 
                    Q(authors__icontains=query) | 
                    Q(isbn13__icontains=query) |
                    listing_text_match,
                    region=region
                )
                if course:
                    course_filter = Q(listings__course_name=course, listings__status='active')
                    if school:
                        course_filter &= Q(listings__school__name=school)
                    local_books = local_books.filter(course_filter)
                
                # Limit local books to 100 to prevent memory explosion when merging
                # with Google Books API results in Python.
                local_books = local_books.distinct().order_by('-created_at')[:100]
            
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

        isbns = [item['isbn'] for item in results if item.get('isbn')]
        ids = [item['id'] for item in results if item.get('id')]
        books_by_isbn = {}
        books_by_id = {}
        if isbns or ids:
            global_active_filter = Q(listings__status='active')
            local_active_filter = Q(listings__status='active')
            if school:
                local_active_filter &= Q(listings__school__name=school)

            books = Book.objects.filter(Q(isbn13__in=isbns) | Q(id__in=ids), region=region).annotate(
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

        conditions_by_book_id = {}
        if books_by_id:
            condition_filter = {'book_id__in': list(books_by_id.keys()), 'status': 'active'}
            if school:
                condition_filter['school__name'] = school
            condition_rows = Listing.objects.filter(**condition_filter).values_list('book_id', 'condition').distinct()
            for book_id, condition in condition_rows:
                conditions_by_book_id.setdefault(book_id, []).append(condition)

        subscription_by_book_id = {}
        if request.user.is_authenticated and books_by_isbn:
            subs = Subscription.objects.filter(region=region, 
                user=request.user,
                book_id__in=[book.id for book in books_by_isbn.values()],
            )
            subscription_by_book_id = {sub.book_id: sub.id for sub in subs}

        enhanced_results = []
        for item in results:
            isbn = item.get('isbn')
            local_id = item.get('id')
            book = books_by_id.get(local_id) or (books_by_isbn.get(isbn) if isbn else None)
            subscription_id = subscription_by_book_id.get(book.id) if book else None

            enhanced_results.append({
                'id': str(book.id) if book else '',
                'title': item.get('title', ''),
                'author': item.get('authors', ''),
                'isbn': isbn or '',
                'coverUrl': item.get('cover_url', ''),
                'publisher': item.get('publisher', ''),
                'published_date': item.get('published_date', ''),
                'source': item.get('source', 'manual'),
                'debug_source': item.get('debug_source'),
                'activeListings': book.global_active_listings_count if book else 0,
                'localActiveListings': book.local_active_listings_count if book else 0,
                'minPrice': (book.min_active_price or 0) if book else 0,
                'waitlistCount': book.waitlist_count if book else 0,
                'conditions': conditions_by_book_id.get(book.id, []) if book else [],
                'is_subscribed': subscription_id is not None,
                'subscription_id': subscription_id,
            })

        filtered_results = []
        for item in enhanced_results:
            item_in_stock = item['activeListings'] > 0
            if in_stock_required and not item_in_stock:
                continue
            if filter_price:
                if not item_in_stock:
                    continue
                min_price = item['minPrice']
                if min_price is None:
                    continue
                if p_min is not None and min_price < p_min:
                    continue
                if p_max is not None and min_price > p_max:
                    continue
            if allowed_conditions is not None:
                conds = item.get('conditions', [])
                if not set(conds).intersection(allowed_conditions):
                    continue
            filtered_results.append(item)

        facet_base_q = Q(status='active', book__region=region)
        if school:
            facet_base_q &= Q(school__name=school)
            
        if category or (course and not query):
            if category:
                facet_base_q &= Q(category__slug=category)
            if course:
                facet_base_q &= Q(course_name=course)
        else:
            query_stripped = query.strip() if query else ''
            is_isbn = query_stripped.isdigit() and len(query_stripped) in (10, 13) if query_stripped else False
            if is_isbn:
                facet_base_q &= Q(book__isbn13=query_stripped)
            elif query_stripped:
                facet_base_q &= (
                    Q(book__title__icontains=query_stripped) |
                    Q(book__authors__icontains=query_stripped) |
                    Q(book__isbn13__icontains=query_stripped) |
                    Q(course_name__icontains=query_stripped) |
                    Q(professor_name__icontains=query_stripped)
                )

        from core.models import Category
        # IMPORTANT: When calculating facets, we DO NOT apply the facet's own condition!
        # Re-build query base without category/course for facet computation.
        
        # Base query that DOES NOT have category or course applied (we will apply them selectively)
        base_facet_no_cat_no_course = Q(status='active', book__region=region)
        if school:
            base_facet_no_cat_no_course &= Q(school__name=school)
            
        if category or (course and not query):
            # In this branch, query is ignored by the main search.
            pass
        else:
            query_stripped = query.strip() if query else ''
            is_isbn = query_stripped.isdigit() and len(query_stripped) in (10, 13) if query_stripped else False
            if is_isbn:
                base_facet_no_cat_no_course &= Q(book__isbn13=query_stripped)
            elif query_stripped:
                base_facet_no_cat_no_course &= (
                    Q(book__title__icontains=query_stripped) |
                    Q(book__authors__icontains=query_stripped) |
                    Q(book__isbn13__icontains=query_stripped) |
                    Q(course_name__icontains=query_stripped) |
                    Q(professor_name__icontains=query_stripped)
                )
                
        # 1. Category Facets (apply course, but not category)
        cat_q = base_facet_no_cat_no_course
        if course:
            cat_q &= Q(course_name=course)
        cat_rows = Listing.objects.filter(cat_q).exclude(category__isnull=True).values('category__slug').annotate(c=Count('book_id', distinct=True))
        category_counts = {row['category__slug']: row['c'] for row in cat_rows}
        all_categories = Category.objects.filter(region=region)
        category_facets = [{'value': c.slug, 'count': category_counts.get(c.slug, 0)} for c in all_categories]
        
        # 2. Course Facets (apply category, but not course)
        course_q = base_facet_no_cat_no_course
        if category:
            course_q &= Q(category__slug=category)
        course_rows = Listing.objects.filter(course_q).exclude(course_name='').values('course_name').annotate(c=Count('book_id', distinct=True))
        course_counts_dict = {row['course_name']: row['c'] for row in course_rows}
        
        courses_q = Q(status='active', book__region=region)
        if school:
            courses_q &= Q(school__name=school)
        if category:
            courses_q &= Q(category__slug=category)
        top_courses = Listing.objects.filter(courses_q).exclude(course_name='').values('course_name').annotate(c=Count('id')).order_by('-c', 'course_name')[:20]
        frontend_course_names = [row['course_name'] for row in top_courses]
        course_facets = [{'value': c, 'count': course_counts_dict.get(c, 0)} for c in frontend_course_names]
        
        # 3. Condition Facets (apply category AND course, but not condition)
        cond_q = base_facet_no_cat_no_course
        if category:
            cond_q &= Q(category__slug=category)
        if course:
            cond_q &= Q(course_name=course)
        cond_rows = Listing.objects.filter(cond_q).values('condition').annotate(c=Count('book_id', distinct=True))
        condition_counts = {row['condition']: row['c'] for row in cond_rows}
        condition_keys = ['new', 'like_new', 'noted', 'damaged']
        condition_facets = [{'value': c, 'count': condition_counts.get(c, 0)} for c in condition_keys]
        
        facets = {
            'condition': condition_facets,
            'category': category_facets,
            'course': course_facets,
        }

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginated_results = paginator.paginate_queryset(filtered_results, request)

        if paginated_results is not None:
            response = paginator.get_paginated_response(paginated_results)
            response.data['google_unavailable'] = google_unavailable
            response.data['results_truncated'] = results_truncated
            response.data['facets'] = facets
            return response
            
        return Response({
            'google_unavailable': google_unavailable,
            'results_truncated': results_truncated,
            'facets': facets,
            'results': filtered_results
        })

class CourseListView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'search'

    def get(self, request):
        school = request.GET.get('school', '')
        category = request.GET.get('category', '')

        region = get_region(request)
        # Hand-rolled cache rather than cache_page: that keyed on the URL
        # alone, while the region comes from the X-Region header or cookie as
        # well, so Hong Kong could be served Taiwan's course list for an
        # hour. Stamped with the listing_list generation, it is also
        # invalidated the moment a listing changes instead of going stale.
        cache_key = region_versioned_key(region, 'listing_list', 'courses', school, category)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        courses = Listing.objects.filter(region=region, status='active').exclude(course_name__exact='')
        if school:
            courses = courses.filter(school__name=school)
        if category:
            courses = courses.filter(category__slug=category)
        
        course_counts = courses.values('course_name').annotate(
            count=Count('id')
        ).order_by('-count', 'course_name')[:20]
        
        results = [{'value': item['course_name'], 'count': item['count']} for item in course_counts]
        cache.set(cache_key, results, timeout=LISTING_CACHE_TTL)
        return Response(results)

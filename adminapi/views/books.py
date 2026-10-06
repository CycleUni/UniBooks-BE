"""Correcting a book's catalogue record from the admin.

A Book can end up under a wrong ISBN — a camera misread saved before scans
were checked, or a typo in the sell form — and then carries whatever the
seller typed in by hand. These views let a region's admin look the right ISBN
up in the external catalogues, edit the record, and fold it into the book
that already has that ISBN.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status, views
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from catalog.merge import merge_book_into
from catalog.models import Book
from catalog.serializers import BookSerializer
from catalog.services import (
    clean_cover_url,
    clean_publisher,
    get_google_books_by_isbn,
    get_isbnnet_book_by_isbn,
    get_open_library_book_by_isbn,
    validate_book_isbn,
)
from catalog.services.engines import (
    ISBN_FALLBACK_ORDER,
    SOURCE_BY_ENGINE,
    engine_order,
    lookup_in_order,
    region_search_engines,
)
from core.cache import bump_cache_version, safe_cache_delete
from core.models import AuditEvent
from listings.models import Listing
from subscriptions.models import Subscription

from ..pagination import AdminPagination
from ..permissions import IsRegionManager

ISBN_LOOKUPS = {
    'googlebooks': get_google_books_by_isbn,
    'isbnnet': get_isbnnet_book_by_isbn,
    'openlibrary': get_open_library_book_by_isbn,
}

# Field name -> longest value the column takes.
TEXT_FIELDS = {'title': 255, 'authors': 512, 'publisher': 255, 'published_date': 50}


def _invalid_isbn():
    return Response({"error": {"code": "listing.errInvalidIsbn"}}, status=status.HTTP_400_BAD_REQUEST)


def _invalid_field():
    return Response({"error": {"code": "admin.errInvalidField"}}, status=status.HTTP_400_BAD_REQUEST)


def _isbn_taken(other):
    return Response(
        {"error": {"code": "admin.errBookIsbnTaken", "existing_book": _book_ref(other)}},
        status=status.HTTP_409_CONFLICT,
    )


def _text(found, key):
    # Catalogues can send a key with a JSON null; the form wants strings.
    value = found.get(key)
    return value.strip() if isinstance(value, str) else ''


def _book_ref(book):
    return {'id': book.pk, 'title': book.title} if book else None


class AdminBookListSerializer(BookSerializer):
    active_listings = serializers.IntegerField(read_only=True)
    request_count = serializers.IntegerField(read_only=True)
    pending_review = serializers.SerializerMethodField()

    class Meta(BookSerializer.Meta):
        exclude = ('reviewed_by',)
        read_only_fields = BookSerializer.Meta.read_only_fields + ('reviewed_at',)

    def get_pending_review(self, book):
        return book.source == 'manual' and book.reviewed_at is None


# `?review=` on the book list: the manual books still to check, or those an
# admin has confirmed no catalogue has.
REVIEW_FILTERS = {
    'pending': Q(source='manual', reviewed_at__isnull=True),
    'confirmed': Q(source='manual', reviewed_at__isnull=False),
}


def _count_of(qs):
    # A subquery per count, not two joins: joining both listings and
    # subscriptions multiplies one by the other before counting.
    counted = qs.filter(book=OuterRef('pk')).order_by().values('book').annotate(n=Count('pk')).values('n')
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


def _with_counts(qs):
    return qs.annotate(
        active_listings=_count_of(Listing.objects.filter(status='active')),
        request_count=_count_of(Subscription.objects.all()),
    )


def _scoped_books(request):
    qs = Book.objects.select_related('region')
    if not request.user.is_superuser:
        qs = qs.filter(region__in=request.user.managed_regions.all())
    return qs


class AdminBookListView(generics.ListAPIView):
    """GET /api/v1/admin/books/?q=&region=&review=

    Every book of the regions the admin manages, newest first, so one that
    never sold, and so never reaches the stats ranking, can still be found
    and corrected. `q` matches the title, the authors or the ISBN. `review`
    narrows to the manual books still to review (`pending`) or already
    confirmed (`confirmed`).
    """
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminBookListSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = _scoped_books(self.request)
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            match = Q(title__icontains=q) | Q(authors__icontains=q)
            # ISBNs are stored bare; an admin may paste one with hyphens.
            digits = q.replace('-', '').replace(' ', '')
            if digits:
                match |= Q(isbn13__icontains=digits)
            qs = qs.filter(match)
        # Uppercased: the frontend spells the region as the URL does.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(region_id=region)
        review = REVIEW_FILTERS.get(self.request.query_params.get('review') or '')
        if review is not None:
            qs = qs.filter(review)
        return _with_counts(qs).order_by('-created_at', '-pk')


class _AdminBookView(views.APIView):
    permission_classes = [IsAdminUser, IsRegionManager]

    def get_book(self, request, pk, qs=None):
        book = get_object_or_404(_scoped_books(request) if qs is None else qs, pk=pk)
        self.check_object_permissions(request, book)
        return book

    @staticmethod
    def other_book_with(book, isbn):
        return Book.objects.filter(region_id=book.region_id, isbn13=isbn).exclude(pk=book.pk).first()


class AdminBookLookupView(_AdminBookView):
    """GET /api/v1/admin/books/<id>/lookup/?isbn=

    What the external catalogues hold for `isbn`, in the order the book's
    region allows them. Read-only: the admin reviews it in the edit form
    before saving. `existing_book` is the region's other book already under
    this ISBN, which saving would merge into.
    """

    def get(self, request, pk):
        book = self.get_book(request, pk)
        isbn = validate_book_isbn(request.query_params.get('isbn'))
        if not isbn:
            return _invalid_isbn()

        allowed = region_search_engines(book.region)
        found, engine_used, _meta, _ = lookup_in_order(
            isbn, engine_order(None, allowed, ISBN_FALLBACK_ORDER), ISBN_LOOKUPS, allowed,
        )
        if not found:
            return Response({"error": {"code": "admin.errBookLookupNotFound"}}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'isbn13': isbn,
            'title': _text(found, 'title'),
            'authors': _text(found, 'authors'),
            'publisher': clean_publisher(_text(found, 'publisher')),
            'published_date': _text(found, 'published_date'),
            'cover_url': _text(found, 'cover_url'),
            'source': SOURCE_BY_ENGINE[engine_used],
            'existing_book': _book_ref(self.other_book_with(book, isbn)),
        })


class AdminBookDetailView(_AdminBookView):
    """GET / PATCH /api/v1/admin/books/<id>/

    GET is the record with its listing and request counts. PATCH edits it. An `isbn13` another book of the region already has is
    refused with 409 and that book, unless `merge` is true: then this book's
    listings and subscriptions move to that one and this book is deleted,
    leaving that book's own details as they are.
    """

    def get(self, request, pk):
        book = self.get_book(request, pk, _with_counts(_scoped_books(request)))
        return Response(AdminBookListSerializer(book).data)

    def patch(self, request, pk):
        book = self.get_book(request, pk)
        if not isinstance(request.data, dict):
            return _invalid_field()
        updates, error = self.parse(request.data)
        if error:
            return error
        isbn = updates.get('isbn13')
        merge = request.data.get('merge') is True

        target = self.other_book_with(book, isbn) if isbn else None
        if target is not None and not merge:
            return _isbn_taken(target)

        try:
            with transaction.atomic():
                # Locked in id order, so two admins merging A into B and B
                # into A wait for each other instead of deadlocking. Holding
                # the source row also holds back a listing or request being
                # added to it, which the merge would otherwise delete.
                ids = sorted({book.pk} | ({target.pk} if target else set()))
                locked = {b.pk: b for b in Book.objects.select_for_update().filter(pk__in=ids).order_by('pk')}
                if book.pk not in locked:
                    raise Http404
                book = locked[book.pk]
                # Another save may have claimed the ISBN, or released it,
                # while this request waited for the locks.
                target = self.other_book_with(book, isbn) if isbn else None
                if target is not None and (not merge or target.pk not in locked):
                    return _isbn_taken(target)

                if target is not None:
                    before = _book_snapshot(book)
                    moved = merge_book_into(book, locked[target.pk])
                    AuditEvent.objects.create(
                        user=request.user,
                        kind='admin.book_merged',
                        meta={
                            'book_id': before['id'], 'book': before,
                            'into_book_id': target.pk, 'into_isbn13': target.isbn13,
                            **moved,
                        },
                    )
                elif updates:
                    if 'isbn13' in updates and updates['isbn13'] != book.isbn13 and book.reviewed_at:
                        # The check was that the catalogues have nothing
                        # for the old ISBN; the new one has not been looked up.
                        updates['reviewed_at'] = None
                        updates['reviewed_by_id'] = None
                    AuditEvent.objects.create(
                        user=request.user,
                        kind='admin.book_updated',
                        meta={
                            'book_id': book.pk,
                            'before': {f: _audit_value(getattr(book, f)) for f in updates},
                            'after': {f: _audit_value(v) for f, v in updates.items()},
                        },
                    )
                    for field, value in updates.items():
                        setattr(book, field, value)
                    book.save(update_fields=list(updates))
        except IntegrityError:
            # The unique (region, isbn13) constraint: a book took this ISBN
            # between the check above and the save.
            return _isbn_taken(self.other_book_with(book, isbn))

        if target is not None:
            # The listings' own saves already did this, but a book with only
            # requests on it has none to save, and its old page stays cached.
            _invalidate_book_caches(target)
            return Response({'merged_into': target.pk, 'book': BookSerializer(target).data})
        if updates:
            _invalidate_book_caches(book)
        return Response({'merged_into': None, 'book': BookSerializer(book).data})

    @staticmethod
    def parse(data):
        """The changes `data` asks for, or a 400 response."""
        updates = {}
        for field, max_length in TEXT_FIELDS.items():
            if field in data:
                value = data[field]
                if not isinstance(value, str):
                    return None, _invalid_field()
                updates[field] = value.strip()[:max_length]
        if 'title' in updates and not updates['title']:
            return None, _invalid_field()
        if 'publisher' in updates:
            updates['publisher'] = clean_publisher(updates['publisher'])
        if 'cover_url' in data:
            cover = clean_cover_url(data['cover_url'])
            if cover is None:
                return None, _invalid_field()
            updates['cover_url'] = cover
        if 'source' in data:
            if data['source'] not in dict(Book.SOURCE_CHOICES):
                return None, _invalid_field()
            updates['source'] = data['source']
        if 'isbn13' in data:
            raw = data['isbn13']
            if raw in ('', None):
                updates['isbn13'] = None
            else:
                isbn = validate_book_isbn(raw)
                if not isbn:
                    return None, _invalid_isbn()
                updates['isbn13'] = isbn
        return updates, None


class AdminBookConfirmManualView(_AdminBookView):
    """POST /api/v1/admin/books/<id>/confirm-manual/

    Records that an admin looked the manual book up and no catalogue has it,
    which takes it out of the review queue. A book with a catalogue record
    is corrected through PATCH instead, which gives it that source.
    """

    def post(self, request, pk):
        with transaction.atomic():
            book = self.get_book(request, pk, _scoped_books(request).select_for_update(of=('self',)))
            if book.source != 'manual':
                return Response({"error": {"code": "admin.errBookNotManual"}}, status=status.HTTP_400_BAD_REQUEST)
            _confirm_manual([book], request.user)
        book = _with_counts(_scoped_books(request)).get(pk=book.pk)
        return Response(AdminBookListSerializer(book).data)


# One page of the admin's book list at its largest.
MAX_BULK_REVIEW = 100


def _bulk_ids(request):
    """The book ids a bulk review request names, or None when malformed."""
    ids = request.data.get('ids') if isinstance(request.data, dict) else None
    if (
        not isinstance(ids, list) or not ids or len(ids) > MAX_BULK_REVIEW
        # bool is an int subclass; True is not book 1.
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    ):
        return None
    return set(ids)


class _AdminBookBulkReviewView(views.APIView):
    """A review change for several manual books, as picked from the book
    list. A book that is not the admin's to see, is not manual or is
    already in the wanted state is skipped, not an error: the response
    lists, under `result_key`, the ids this request changed."""
    permission_classes = [IsAdminUser, IsRegionManager]
    result_key = ''
    # Which manual books the change applies to.
    applies_to = Q()

    def post(self, request):
        ids = _bulk_ids(request)
        if ids is None:
            return _invalid_field()
        with transaction.atomic():
            books = list(
                _scoped_books(request)
                .select_for_update(of=('self',))
                .filter(self.applies_to, pk__in=ids, source='manual')
                .order_by('pk')
            )
            self.apply(books, request.user)
        return Response({self.result_key: [b.pk for b in books]})

    def apply(self, books, user):
        raise NotImplementedError


class AdminBookBulkConfirmManualView(_AdminBookBulkReviewView):
    """POST /api/v1/admin/books/confirm-manual/  {"ids": [...]} -> {"confirmed": [...]}"""
    result_key = 'confirmed'
    applies_to = Q(reviewed_at__isnull=True)

    def apply(self, books, user):
        _confirm_manual(books, user)


class AdminBookBulkReopenReviewView(_AdminBookBulkReviewView):
    """POST /api/v1/admin/books/reopen-review/  {"ids": [...]} -> {"reopened": [...]}

    Puts confirmed manual books back in the review queue, for one confirmed
    by mistake or worth a second look.
    """
    result_key = 'reopened'
    applies_to = Q(reviewed_at__isnull=False)

    def apply(self, books, user):
        for book in books:
            book.reviewed_at = None
            book.reviewed_by = None
            book.save(update_fields=['reviewed_at', 'reviewed_by'])
            AuditEvent.objects.create(
                user=user,
                kind='admin.book_review_reopened',
                meta={'book_id': book.pk, 'isbn13': book.isbn13, 'title': book.title},
            )


def _confirm_manual(books, user):
    """Marks locked manual books reviewed by `user`, each with its audit record."""
    now = timezone.now()
    for book in books:
        if book.reviewed_at is not None:
            continue
        book.reviewed_at = now
        book.reviewed_by = user
        book.save(update_fields=['reviewed_at', 'reviewed_by'])
        AuditEvent.objects.create(
            user=user,
            kind='admin.book_manual_confirmed',
            meta={'book_id': book.pk, 'isbn13': book.isbn13, 'title': book.title},
        )


def _audit_value(value):
    """A field's value as the audit log's JSON can hold it."""
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _book_snapshot(book):
    """Everything about a book row, for an audit record outliving it."""
    return {
        'id': book.pk, 'isbn13': book.isbn13, 'title': book.title, 'authors': book.authors,
        'publisher': book.publisher, 'published_date': book.published_date,
        'cover_url': book.cover_url, 'source': book.source,
        'created_at': book.created_at.isoformat() if book.created_at else None,
    }


def _invalidate_book_caches(book):
    """Every cached page that shows this book's details."""
    bump_cache_version('book_detail')
    bump_cache_version('listing_list')
    for listing_id in book.listings.values_list('pk', flat=True):
        bump_cache_version(f'listing:{listing_id}')
    safe_cache_delete('home_waitlist')

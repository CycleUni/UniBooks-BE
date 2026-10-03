"""Correcting a book's catalogue record from the admin.

A Book can end up under a wrong ISBN — a camera misread saved before scans
were checked, or a typo in the sell form — and then carries whatever the
seller typed in by hand. These views let a region's admin look the right ISBN
up in the external catalogues, edit the record, and fold it into the book
that already has that ISBN.
"""

from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status, views
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


class _AdminBookView(views.APIView):
    permission_classes = [IsAdminUser, IsRegionManager]

    def get_book(self, request, pk):
        qs = Book.objects.select_related('region')
        if not request.user.is_superuser:
            qs = qs.filter(region__in=request.user.managed_regions.all())
        book = get_object_or_404(qs, pk=pk)
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
    """PATCH /api/v1/admin/books/<id>/

    Edits the record. An `isbn13` another book of the region already has is
    refused with 409 and that book, unless `merge` is true: then this book's
    listings and subscriptions move to that one and this book is deleted,
    leaving that book's own details as they are.
    """

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
                    AuditEvent.objects.create(
                        user=request.user,
                        kind='admin.book_updated',
                        meta={
                            'book_id': book.pk,
                            'before': {f: getattr(book, f) for f in updates},
                            'after': updates,
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

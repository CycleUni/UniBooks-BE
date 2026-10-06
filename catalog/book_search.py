"""Matching a typed search against a book: title, authors or ISBN."""
from django.db.models import Q


def book_search_q(q, prefix=''):
    """Q matching books whose title or authors contain `q`, or whose ISBN
    contains it once hyphens and spaces are dropped — ISBNs are stored bare,
    and an admin may paste one as printed. `prefix` reaches the book through
    a relation ('book__', 'listing__book__')."""
    match = Q(**{f'{prefix}title__icontains': q}) | Q(**{f'{prefix}authors__icontains': q})
    digits = q.replace('-', '').replace(' ', '')
    if digits:
        match |= Q(**{f'{prefix}isbn13__icontains': digits})
    return match

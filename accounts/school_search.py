"""Matching a typed search against schools, in any language.

A school is known by its canonical English name, its short code, its email
domain, and a localized name per language in `translations`. Searching only
`name` meant an admin typing 臺灣大學 found nothing: the Chinese name lives in
`translations`, which no search looked at.
"""
from django.db.models import Q


def school_search_q(q, prefix=''):
    """Q matching schools whose name in any language, code or email domain
    contains `q`. `prefix` reaches a school through a relation ('school__')."""
    from core.models import Language

    match = (
        Q(**{f'{prefix}name__icontains': q})
        | Q(**{f'{prefix}code__icontains': q})
        | Q(**{f'{prefix}email_domain__icontains': q})
    )
    # One key lookup per language rather than icontains over the whole JSON:
    # that would also match the keys ("name", "zh-TW") and the escapes a
    # non-Postgres backend stores non-ASCII text as.
    for code in Language.objects.values_list('code', flat=True):
        match |= Q(**{f'{prefix}translations__{code}__name__icontains': q})
    return match

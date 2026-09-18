"""School short codes ("NTU", "HKU") and resolving a `?school=` parameter.

A code is only unique *within a region*: Taiwan's HKU (Hung Kuang) and Hong
Kong's HKU are different schools, so a code on its own never identifies a
school — every lookup here takes the region it is meant to search.

The pure helpers (normalize / derive / dedupe) import no models on purpose:
the backfill migration imports them too, and a migration must not depend on
the *current* model classes, only on the historical ones it is handed.
"""
import re

CODE_MAX_LENGTH = 20
CODE_PATTERN = r'^[A-Z0-9-]+$'
# What a form may accept before normalization uppercases it.
CODE_INPUT_PATTERN = r'^[A-Za-z0-9-]+$'
_CODE_RE = re.compile(CODE_PATTERN)
_NOT_CODE_CHARS = re.compile(r'[^A-Z0-9-]')
# What a school gets when nothing usable can be read off its domain. Only
# reachable with a malformed domain (".edu.tw", "_.edu.hk"); the dedupe
# suffix keeps two of those apart.
FALLBACK_CODE = 'SCHOOL'


def normalize_code(value):
    """Uppercase and strip every whitespace, so " ntu " and "N TU" both
    store as "NTU". Does not validate — see is_valid_code."""
    if value is None:
        return ''
    return re.sub(r'\s+', '', str(value)).upper()


def is_valid_code(value):
    return bool(value) and len(value) <= CODE_MAX_LENGTH and bool(_CODE_RE.match(value))


def derive_code(email_domain):
    """The default code for a school: the first label of its email domain,
    uppercased — ntu.edu.tw → NTU, hku.hk → HKU.

    Characters a code cannot hold are dropped rather than rejected, since
    this runs unattended (migration backfill, bulk import, a School saved
    without a code) and has nobody to report an error to.
    """
    first = (email_domain or '').strip().split('.')[0]
    code = _NOT_CODE_CHARS.sub('', first.upper())[:CODE_MAX_LENGTH]
    return code or FALLBACK_CODE


def dedupe_code(base, taken):
    """`base`, or `base-2`, `base-3`, ... — the first not in `taken`.

    `taken` holds the codes already used *in the same region*. The base is
    trimmed so the suffixed code still fits CODE_MAX_LENGTH instead of being
    cut off afterwards (which could collide again).
    """
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f'-{n}'
        candidate = base[:CODE_MAX_LENGTH - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


def resolve_school(region, value):
    """The School a `?school=` value names, searched only inside `region`.

    `value` is a code, matched case-insensitively. Before codes existed the
    frontend sent the English full name, and those live on in shared links
    and in sessionStorage of tabs opened before the deploy — so a value that
    is no school's code is tried as an exact name next, still only in this
    region. Returns None when neither matches (or no region is known):
    callers must treat that as "a school with no listings", not as "no
    school filter", or a mistyped code would widen the results to everyone.
    """
    from accounts.models import School

    if not value or region is None:
        return None
    schools = School.objects.filter(region=region)
    code = normalize_code(value)
    if is_valid_code(code):
        school = schools.filter(code=code).first()
        if school:
            return school
    return schools.filter(name=value).first()


# The id a filter uses for a `?school=` that named no school in the region.
# No row has id 0, so `school_id=0` matches nothing — the same empty result
# the old `school__name=<unknown>` filter gave, without having to thread a
# "match nothing" branch through every Q object in the search view.
NO_SCHOOL_ID = 0


def school_filter_id(region, value):
    """`?school=` turned into what the list/search views filter on.

    None: no school parameter, don't filter. NO_SCHOOL_ID: a parameter that
    names no school in this region, so match nothing. Otherwise the id.
    """
    if not value:
        return None
    school = resolve_school(region, value)
    return school.id if school else NO_SCHOOL_ID


def admin_school_filter_id(request, value):
    """`?school=` on the admin list views: an id as before, or else a code /
    name resolved in the request's region like the public views do."""
    from core.region import get_region

    value = str(value).strip()
    if value.isdigit():
        return int(value)
    return school_filter_id(get_region(request), value)

import re

# ASCII only, like the frontend's /^\d+$/: str.isdigit() also accepts digits
# such as '²' (which int() then rejects) and fullwidth or Arabic-Indic ones
# (which int() reads, so a lookalike ISBN would pass and be stored as a
# different string from the real one).
_ISBN_SHAPE = re.compile(r'[0-9]{13}|[0-9]{9}[0-9X]')


def clean_and_validate_isbn(isbn_str):
    if not isbn_str:
        return None
    raw_isbn = str(isbn_str).replace('-', '').replace(' ', '').upper()
    return raw_isbn if _ISBN_SHAPE.fullmatch(raw_isbn) else None


def isbn_checksum_ok(isbn):
    """Whether a cleaned ISBN-10/13 carries a correct check digit."""
    if len(isbn) == 13 and isbn.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn[:12]))
        return (10 - total % 10) % 10 == int(isbn[12])
    if len(isbn) == 10 and isbn[:9].isdigit():
        total = sum(int(d) * (10 - i) for i, d in enumerate(isbn[:9]))
        total += 10 if isbn[9] == 'X' else int(isbn[9])
        return total % 11 == 0
    return False


def validate_book_isbn(isbn_str):
    """The cleaned ISBN to store on a Book, or None when it can't be a book's.

    Stricter than clean_and_validate_isbn, which stays format-only for reads
    (a lookup that finds nothing is just a 404). Anything written to
    Book.isbn13 must also have a valid check digit and, for 13 digits, the
    978/979 Bookland prefix every ISBN-13 carries: camera misreads produced
    checksum-valid numbers like 6770250629200 (for 9789860629200), and a
    Book saved under one stays wrong for every later visitor.
    """
    isbn = clean_and_validate_isbn(isbn_str)
    if not isbn or not isbn_checksum_ok(isbn):
        return None
    if len(isbn) == 13 and not isbn.startswith(('978', '979')):
        return None
    return isbn


def isbn_forms(isbn):
    """A cleaned ISBN with its other length, when it has one: an ISBN-10 and
    the 978 ISBN-13 are the same book. (979 numbers have no ISBN-10.)"""
    forms = {isbn}
    if len(isbn) == 13 and isbn.startswith('978'):
        core = isbn[3:12]
        total = sum(int(d) * (10 - i) for i, d in enumerate(core))
        check = (11 - total % 11) % 11
        forms.add(core + ('X' if check == 10 else str(check)))
    elif len(isbn) == 10 and isbn[:9].isdigit():
        core = '978' + isbn[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
        forms.add(core + str((10 - total % 10) % 10))
    return forms

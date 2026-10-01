"""What a row that points at a listing keeps of it once the listing is gone.

Orders, conversations and listing reports outlive their listing: a seller may
delete it, and the buyer's order history, the chat about it and the report
against it all have to stay readable. Their `listing` FK is SET_NULL, so each
row copies what it shows and what it is joined on while the listing still
exists:

- `listing_ref`  the listing's id, not a foreign key. An order and its
                 conversation are paired on (listing_ref, buyer), which keeps
                 working after both lose the listing.
- `book_title` / `book_isbn`  what the row is about. No photo URL: the
                 listing's photos are removed from storage with it, while the
                 cover is fetched by ISBN (/api/cover).
- `seller` / `region`  for the models that had no column of their own and
                 reached both through the listing (Conversation, Report).

Filled in `save()` rather than by every view, so a row created from any path
— an API view, the Django admin, a test — carries the snapshot.
"""


def fill_listing_snapshot(obj, *, seller=False, region=False):
    """Copy the listing's details onto `obj` if it has a listing and no snapshot yet."""
    if obj.listing_ref is not None or obj.listing_id is None:
        return
    listing = obj.listing
    obj.listing_ref = listing.pk
    book = listing.book
    obj.book_title = (book.title or '')[:255] if book else ''
    if hasattr(obj, 'book_isbn'):
        obj.book_isbn = (book.isbn13 or '') if book else ''
    if seller and obj.seller_id is None:
        obj.seller_id = listing.seller_id
    if region and obj.region_id is None:
        obj.region_id = listing.region_id

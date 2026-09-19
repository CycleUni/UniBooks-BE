from django.db import migrations

# The only producer of this URL shape was the guess search.views used to fill
# in for a result without a cover (removed in the same change). Open Library
# covers the catalogue actually returned use /b/id/<cover id>-M.jpg instead.
GUESSED_COVER_REGEX = r'^https://covers\.openlibrary\.org/b/isbn/[0-9Xx]+-L\.jpg$'


def clear_guessed_covers(apps, schema_editor):
    """Empty every cover_url that is a guessed Open Library URL.

    By the time a result was given one, Google and Open Library had usually
    already said they had no such book, so the URL almost never pointed at an
    image — 9786263241893, an ISBNnet record, is the case that set off the
    home page's cover request loop. An empty cover_url shows the placeholder
    without a request.
    """
    Book = apps.get_model('catalog', 'Book')
    cleared = Book.objects.filter(cover_url__regex=GUESSED_COVER_REGEX).update(cover_url='')
    if cleared:
        # Book pages and listing lists are cached with the cover URL in them.
        # Best effort: a cache outage must not fail the migration, and the
        # cached copies expire on their own anyway.
        try:
            from core.cache import bump_cache_version
            bump_cache_version('book_detail')
            bump_cache_version('listing_list')
        except Exception:
            pass


class Migration(migrations.Migration):
    """Reversing is a no-op: the guessed URLs are not worth restoring."""

    dependencies = [
        ('catalog', '0007_alter_book_region'),
    ]

    operations = [
        migrations.RunPython(clear_guessed_covers, migrations.RunPython.noop),
    ]

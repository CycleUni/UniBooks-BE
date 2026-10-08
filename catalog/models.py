from django.db import models
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db.models.functions import Upper

class Book(models.Model):
    region = models.ForeignKey('core.Region', on_delete=models.CASCADE, related_name='books')
    SOURCE_CHOICES = [
        ('listed', 'Listed'),
        ('preseed', 'Preseed'),
        ('manual', 'Manual'),
        ('google_api', 'Google Books API'),
        ('openlibrary_api', 'Open Library API'),
        ('isbnnet_api', 'ISBNnet API'),
    ]

    # Unique per region, not globally: every catalogue read, listing and
    # subscription is scoped to a region's own Book rows, so a global
    # constraint let the first region to see an ISBN lock every other region
    # out of listing or subscribing to it.
    isbn13 = models.CharField(max_length=13, null=True, blank=True)
    title = models.CharField(max_length=255)
    authors = models.CharField(max_length=512, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    published_date = models.CharField(max_length=50, blank=True)
    cover_url = models.URLField(max_length=1024, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    # A 'manual' book holds what a seller typed in, which no catalogue
    # vouches for. It waits in the admin's review queue until an admin has
    # checked that the catalogues really have nothing for it; a book found
    # there leaves the queue by taking the catalogue's record and source.
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['region', 'isbn13'], name='book_region_isbn13_uniq'),
        ]
        # On UPPER(column), because that is what Django's icontains compares
        # (UPPER(col::text) LIKE UPPER('%q%')); a trigram index on the bare
        # column, which these replaced, could not serve it, and every
        # keyword search scanned the whole table.
        indexes = [
            GinIndex(OpClass(Upper('title'), name='gin_trgm_ops'), name='book_title_upper_trgm_idx'),
            GinIndex(OpClass(Upper('authors'), name='gin_trgm_ops'), name='book_authors_upper_trgm_idx'),
            GinIndex(OpClass(Upper('isbn13'), name='gin_trgm_ops'), name='book_isbn13_upper_trgm_idx'),
        ]

    def __str__(self):
        return f"{self.title} ({self.isbn13 or 'No ISBN'})"

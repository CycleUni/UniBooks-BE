from django.db import models
from django.contrib.postgres.indexes import GinIndex

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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['region', 'isbn13'], name='book_region_isbn13_uniq'),
        ]
        indexes = [
            GinIndex(
                name='book_trgm_idx',
                fields=['title', 'authors', 'isbn13'],
                opclasses=['gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops']
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.isbn13 or 'No ISBN'})"

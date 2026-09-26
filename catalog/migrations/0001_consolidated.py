# Consolidated on 2026-09-27: replaces this app's 9 historical migrations,
# which every existing database had already applied. `replaces` lets such a
# database treat this file as applied without running it; a new database
# builds the schema from it directly. One-off data migrations that only
# converted rows existing at the time were dropped rather than carried over.

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    replaces = [
        ('catalog', '0001_initial'),
        ('catalog', '0002_alter_book_source'),
        ('catalog', '0003_auto_20260804_1558'),
        ('catalog', '0004_book_book_trgm_idx'),
        ('catalog', '0005_alter_book_source'),
        ('catalog', '0006_book_region'),
        ('catalog', '0007_alter_book_region'),
        ('catalog', '0008_clear_guessed_openlibrary_covers'),
        ('catalog', '0009_book_isbn13_unique_per_region'),
    ]

    dependencies = [
        ('core', '0001_consolidated'),
    ]

    operations = [
        # The gin_trgm_ops indexes below need pg_trgm; a no-op off Postgres.
        TrigramExtension(),
        migrations.CreateModel(
            name='Book',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('isbn13', models.CharField(blank=True, max_length=13, null=True)),
                ('title', models.CharField(max_length=255)),
                ('authors', models.CharField(blank=True, max_length=512)),
                ('publisher', models.CharField(blank=True, max_length=255)),
                ('published_date', models.CharField(blank=True, max_length=50)),
                ('cover_url', models.URLField(blank=True, max_length=1024)),
                ('source', models.CharField(choices=[('listed', 'Listed'), ('preseed', 'Preseed'), ('manual', 'Manual'), ('google_api', 'Google Books API'), ('openlibrary_api', 'Open Library API'), ('isbnnet_api', 'ISBNnet API')], max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('region', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='books', to='core.region')),
            ],
            options={
                'indexes': [django.contrib.postgres.indexes.GinIndex(fields=['title', 'authors', 'isbn13'], name='book_trgm_idx', opclasses=['gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops'])],
                'constraints': [models.UniqueConstraint(fields=('region', 'isbn13'), name='book_region_isbn13_uniq')],
            },
        ),
    ]

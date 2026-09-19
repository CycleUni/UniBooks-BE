import importlib

from django.apps import apps

from catalog.models import Book

migration = importlib.import_module('catalog.migrations.0008_clear_guessed_openlibrary_covers')


def test_clears_only_guessed_open_library_covers(db):
    guessed = Book.objects.create(region_id='TW', isbn13='9786263241893', title='Guessed', source='isbnnet_api',
                                  cover_url='https://covers.openlibrary.org/b/isbn/9786263241893-L.jpg')
    ol_real = Book.objects.create(region_id='TW', isbn13='9780000000017', title='OL', source='openlibrary_api',
                                  cover_url='https://covers.openlibrary.org/b/id/12345-M.jpg')
    google = Book.objects.create(region_id='TW', isbn13='9780000000024', title='Google', source='google_api',
                                 cover_url='https://books.google.com/books/content?id=abc&printsec=frontcover&img=1&zoom=1')
    ncl = Book.objects.create(region_id='TW', isbn13='9780000000031', title='NCL', source='isbnnet_api',
                              cover_url='https://pdsapp.ncl.edu.tw/api/v1/viewer/public/cover/9780000000031')

    migration.clear_guessed_covers(apps, None)

    for book in (guessed, ol_real, google, ncl):
        book.refresh_from_db()
    assert guessed.cover_url == ''
    assert ol_real.cover_url == 'https://covers.openlibrary.org/b/id/12345-M.jpg'
    assert google.cover_url.startswith('https://books.google.com/')
    assert ncl.cover_url.startswith('https://pdsapp.ncl.edu.tw/')

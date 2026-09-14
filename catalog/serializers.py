from rest_framework import serializers
from catalog.models import Book
from catalog.services import clean_publisher


class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = '__all__'
        read_only_fields = ('source', 'created_at', 'region')

    def validate_publisher(self, value):
        # A manual create posts back whatever the search result carried, which
        # before the import fix could still hold Google's wrapping quotes.
        return clean_publisher(value)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Rows imported before clean_publisher existed still hold the quotes
        # in the database. Cleaned on the way out rather than by a data
        # migration, so nothing stored is rewritten.
        if 'publisher' in data:
            data['publisher'] = clean_publisher(data['publisher'])
        return data

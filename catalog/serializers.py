from rest_framework import serializers
from catalog.models import Book

class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = '__all__'
        read_only_fields = ('source', 'created_at', 'region')

from rest_framework import serializers
from subscriptions.models import Subscription

class SubscriptionSerializer(serializers.ModelSerializer):
    bookTitle = serializers.CharField(source='book.title', read_only=True)
    isbn = serializers.CharField(source='book.isbn13', read_only=True, default='')
    bookCoverUrl = serializers.CharField(source='book.cover_url', read_only=True, default='')
    newListingsCount = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = ['id', 'book', 'bookTitle', 'isbn', 'bookCoverUrl', 'newListingsCount', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_newListingsCount(self, obj):
        # Active listings created after the subscription. Views should provide the
        # `new_listings_count` annotation; the per-object query is only a fallback.
        if hasattr(obj, 'new_listings_count'):
            return obj.new_listings_count
        return obj.book.listings.filter(status='active', created_at__gt=obj.created_at).count()

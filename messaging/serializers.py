from rest_framework import serializers
from messaging.models import Conversation
from listings.serializers import ListingSerializer

class ConversationSerializer(serializers.ModelSerializer):
    listing_id = serializers.CharField(source='listing.id', read_only=True)
    listing_title = serializers.CharField(source='listing.book.title', read_only=True)
    listing_photo = serializers.SerializerMethodField()
    listing_price = serializers.IntegerField(source='listing.price', read_only=True)
    listing_condition = serializers.CharField(source='listing.condition', read_only=True)
    listing_course = serializers.CharField(source='listing.course_name', read_only=True, default='')
    other_party = serializers.SerializerMethodField()
    other_party_role = serializers.SerializerMethodField()
    buyer_id = serializers.IntegerField(source='buyer.id', read_only=True)
    seller_id = serializers.IntegerField(source='listing.seller.id', read_only=True)
    latest_message = serializers.SerializerMethodField()
    order_id = serializers.SerializerMethodField()
    order_status = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'listing_id', 'listing_title', 'listing_photo', 'listing_price', 'listing_condition', 'listing_course', 'other_party', 'other_party_role', 'buyer_id', 'seller_id', 'latest_message', 'updated_at', 'order_id', 'order_status']

    # A conversation can accumulate more than one Order over time (declined,
    # then the buyer requests again) — always resolve to the most recently
    # created one. Without explicit ordering, .first() falls back to
    # whatever order the database happens to return rows in, which is not
    # guaranteed to be chronological and can surface a stale, already
    # completed/handed-over order instead of the current pending one.
    def _latest_order(self, obj):
        # Memoised per instance: order_id and order_status both need it.
        if hasattr(obj, '_latest_order_cache'):
            return obj._latest_order_cache
        prefetched = getattr(obj.listing, 'prefetched_orders', None)
        if prefetched is not None:
            # ConversationListView prefetches the listing's orders newest
            # first, so this is a pure in-memory pick.
            mine = [o for o in prefetched if o.buyer_id == obj.buyer_id]
            order = next((o for o in mine if o.status != 'cancelled'), None) or (mine[0] if mine else None)
        else:
            order = obj.listing.orders.filter(buyer=obj.buyer).exclude(status='cancelled').order_by('-created_at').first()
            if not order:
                order = obj.listing.orders.filter(buyer=obj.buyer).order_by('-created_at').first()
        obj._latest_order_cache = order
        return order

    def get_order_id(self, obj):
        order = self._latest_order(obj)
        return str(order.id) if order else None

    def get_order_status(self, obj):
        order = self._latest_order(obj)
        return order.status if order else None

    def get_other_party(self, obj):
        request = self.context.get('request')
        if not request:
            return ""
        if obj.buyer_id == request.user.id:
            user = obj.listing.seller
        else:
            user = obj.buyer
        return user.display_name

    def get_other_party_role(self, obj):
        request = self.context.get('request')
        if not request:
            return ""
        return "seller" if obj.buyer_id == request.user.id else "buyer"

    def get_latest_message(self, obj):
        return obj.latest_message_body if obj.latest_message_body else ""

    def get_listing_photo(self, obj):
        if obj.listing.photos and len(obj.listing.photos) > 0:
            return obj.listing.photos[0]
        return obj.listing.book.cover_url if obj.listing.book else ''




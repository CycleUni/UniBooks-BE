from rest_framework import serializers
from messaging.models import Conversation
from listings.serializers import ListingSerializer
from accounts.serializers import school_name_in_region

class ConversationSerializer(serializers.ModelSerializer):
    # The original listing's id even once it is deleted: the chat links to the
    # listing page, which then says the listing no longer exists. Title and
    # ISBN come from the snapshot (listings/snapshot.py); price, condition and
    # course are only shown while the listing exists and are null after.
    listing_id = serializers.CharField(source='listing_ref', read_only=True)
    listing_title = serializers.CharField(source='book_title', read_only=True)
    listing_isbn = serializers.CharField(source='book_isbn', read_only=True)
    listing_deleted = serializers.BooleanField(read_only=True)
    listing_photo = serializers.SerializerMethodField()
    listing_price = serializers.IntegerField(source='listing.price', read_only=True)
    listing_condition = serializers.CharField(source='listing.condition', read_only=True)
    listing_course = serializers.CharField(source='listing.course_name', read_only=True, default='')
    other_party = serializers.SerializerMethodField()
    other_party_role = serializers.SerializerMethodField()
    # Display names are not unique; the school tells two 周恭煥 apart without
    # exposing anything private. See accounts.serializers.school_name_in_region.
    other_party_school_name = serializers.SerializerMethodField()
    other_party_avatar_url = serializers.SerializerMethodField()
    buyer_id = serializers.IntegerField(source='buyer.id', read_only=True)
    seller_id = serializers.IntegerField(read_only=True)
    latest_message = serializers.SerializerMethodField()
    order_id = serializers.SerializerMethodField()
    order_status = serializers.SerializerMethodField()
    # The agreed meetup, shown on the chat's meetup card.
    order_meetup_time = serializers.SerializerMethodField()
    order_meetup_location = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'listing_id', 'listing_title', 'listing_isbn', 'listing_deleted', 'listing_photo', 'listing_price', 'listing_condition', 'listing_course', 'other_party', 'other_party_role', 'other_party_school_name', 'other_party_avatar_url', 'buyer_id', 'seller_id', 'latest_message', 'updated_at', 'order_id', 'order_status', 'order_meetup_time', 'order_meetup_location']

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
        prefetched = getattr(obj.listing, 'prefetched_orders', None) if obj.listing else None
        if prefetched is not None:
            # ConversationListView prefetches the listing's orders newest
            # first, so this is a pure in-memory pick.
            mine = [o for o in prefetched if o.buyer_id == obj.buyer_id]
            order = next((o for o in mine if o.status != 'cancelled'), None) or (mine[0] if mine else None)
        else:
            # Paired on listing_ref so a deleted listing's chat still finds
            # its order.
            from orders.models import Order
            mine = Order.objects.filter(listing_ref=obj.listing_ref, buyer=obj.buyer).order_by('-created_at')
            order = mine.exclude(status='cancelled').first() or mine.first()
        obj._latest_order_cache = order
        return order

    def get_order_id(self, obj):
        order = self._latest_order(obj)
        return str(order.id) if order else None

    def get_order_status(self, obj):
        order = self._latest_order(obj)
        return order.status if order else None

    def get_order_meetup_time(self, obj):
        order = self._latest_order(obj)
        # Formatted as the order endpoints format it (in the server's zone).
        return serializers.DateTimeField().to_representation(order.meetup_time) if order and order.meetup_time else None

    def get_order_meetup_location(self, obj):
        order = self._latest_order(obj)
        return order.meetup_location if order else ''

    def _other_party_user(self, obj):
        request = self.context.get('request')
        if not request:
            return None
        return obj.seller if obj.buyer_id == request.user.id else obj.buyer

    def get_other_party(self, obj):
        user = self._other_party_user(obj)
        return user.display_name if user else ""

    def get_other_party_school_name(self, obj):
        return school_name_in_region(self._other_party_user(obj), self.context.get('request'))

    def get_other_party_avatar_url(self, obj):
        user = self._other_party_user(obj)
        return user.public_avatar_url if user else ""

    def get_other_party_role(self, obj):
        request = self.context.get('request')
        if not request:
            return ""
        return "seller" if obj.buyer_id == request.user.id else "buyer"

    def get_latest_message(self, obj):
        return obj.latest_message_body if obj.latest_message_body else ""

    def get_listing_photo(self, obj):
        # The photos are deleted from storage with the listing; the frontend
        # shows the cover by ISBN instead.
        listing = obj.listing
        if listing is None:
            return ''
        if listing.photos:
            return listing.photos[0]
        return listing.book.cover_url if listing.book else ''




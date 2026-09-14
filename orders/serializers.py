from rest_framework import serializers
from core.region import get_region
from .models import Order, Review
from listings.models import Listing
from accounts.models import User
from accounts.serializers import school_name_in_region


def visible_conversations(user):
    """Conversations still in `user`'s inbox (not deleted from their side).

    Mirrors messaging.views.ConversationListView's exclusion.
    """
    from django.db.models import Q
    from messaging.models import Conversation
    return Conversation.objects.exclude(
        Q(buyer=user, buyer_deleted_at__isnull=False) |
        Q(listing__seller=user, seller_deleted_at__isnull=False)
    )


class OrderSerializer(serializers.ModelSerializer):
    listing_title = serializers.CharField(source='listing.book.title', read_only=True)
    buyer_name = serializers.CharField(source='buyer.display_name', read_only=True)
    seller_name = serializers.CharField(source='seller.display_name', read_only=True)
    has_reviewed = serializers.SerializerMethodField()
    # Disambiguate the other party beyond a display name, which is not unique
    # — see accounts.serializers.school_name_in_region.
    buyer_avatar_url = serializers.CharField(source='buyer.avatar_url', read_only=True, default='')
    seller_avatar_url = serializers.CharField(source='seller.avatar_url', read_only=True, default='')
    buyer_school_name = serializers.SerializerMethodField()
    seller_school_name = serializers.SerializerMethodField()
    # The buyer/seller conversation this order was arranged in, so the order
    # page can link back to it. Null when there is none, or when the viewer
    # has deleted it from their inbox: the messages page only opens chats in
    # that inbox, so a link would lead nowhere.
    conversation_id = serializers.SerializerMethodField()

    def get_buyer_school_name(self, obj):
        return school_name_in_region(obj.buyer, self.context.get('request'))

    def get_seller_school_name(self, obj):
        return school_name_in_region(obj.seller, self.context.get('request'))

    def get_conversation_id(self, obj):
        if hasattr(obj, 'conversation_id_annotated'):
            conv_id = obj.conversation_id_annotated
            return str(conv_id) if conv_id else None
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        conv_id = visible_conversations(request.user).filter(
            listing_id=obj.listing_id, buyer_id=obj.buyer_id
        ).values_list('id', flat=True).first()
        return str(conv_id) if conv_id else None

    def get_has_reviewed(self, obj):
        # OrderViewSet.get_queryset annotates this; the query below is only
        # the fallback for a serializer built on an un-annotated instance.
        annotated = getattr(obj, 'has_reviewed_annotated', None)
        if annotated is not None:
            return bool(annotated)
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return Review.objects.filter(order=obj, reviewer=request.user).exists()
    
    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('buyer', 'seller', 'region', 'currency', 'status', 'total_amount', 'created_at', 'updated_at')

    def validate(self, attrs):
        listing = attrs.get('listing')
        
        # Ensure the listing is available
        if listing.status != 'active':
            raise serializers.ValidationError({"listing": "checkout.errListingUnavailable"})

        # The listing page is reachable from any region's site (it shows the
        # listing in its own currency and warns about the mismatch), but an
        # order placed from there would be filed under the wrong region and
        # its amount read in the wrong currency — TWD 350 recorded as HKD 350.
        region = get_region(self.context['request'])
        if region is not None and listing.region_id != region.pk:
            raise serializers.ValidationError({"listing": "checkout.errRegionMismatch"})

        # Ensure the buyer is not the seller
        if self.context['request'].user == listing.seller:
            raise serializers.ValidationError({"listing": "checkout.errOwnListing"})
            
        # Ensure buyer and seller have chatted before placing an order
        from messaging.models import Conversation
        has_chatted = Conversation.objects.filter(
            buyer=self.context['request'].user,
            listing=listing
        ).exists()
        if not has_chatted:
            raise serializers.ValidationError({"listing": "checkout.errNoChat"})
            
        return attrs

class OrderStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ('status', 'cancel_reason', 'meetup_time', 'meetup_location')
        
    def validate_status(self, value):
        valid_transitions = {
            'pending': ['accepted', 'cancelled'],
            'accepted': ['handed_over', 'cancelled'],
            'handed_over': ['completed', 'cancelled'],
            'completed': [],
            'cancelled': []
        }
        
        current_status = self.instance.status
        if value not in valid_transitions.get(current_status, []):
            raise serializers.ValidationError(f"Cannot transition from {current_status} to {value}.")
            
        return value

class ReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source='reviewer.display_name', read_only=True)
    reviewee_name = serializers.CharField(source='reviewee.display_name', read_only=True)

    class Meta:
        model = Review
        fields = '__all__'
        read_only_fields = ('reviewer', 'reviewee', 'created_at')
        validators = []  # Bypass auto-generated UniqueTogetherValidator since fields are read-only

    def validate(self, attrs):
        # The model column is a bare PositiveSmallIntegerField, so anything
        # from 0 to 32767 used to be accepted and fed straight into
        # User.average_rating. A no-show report carries no rating at all.
        is_no_show = attrs.get('is_no_show', False)
        rating = attrs.get('rating')
        if is_no_show:
            attrs['rating'] = None
        elif rating is None or not (1 <= rating <= 5):
            raise serializers.ValidationError({"rating": "order.errRatingRange"})
        return attrs

from rest_framework import serializers
from .models import Order, Review
from listings.models import Listing
from accounts.models import User

class OrderSerializer(serializers.ModelSerializer):
    listing_title = serializers.CharField(source='listing.book.title', read_only=True)
    buyer_name = serializers.CharField(source='buyer.display_name', read_only=True)
    seller_name = serializers.CharField(source='seller.display_name', read_only=True)
    has_reviewed = serializers.SerializerMethodField()
    
    def get_has_reviewed(self, obj):
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

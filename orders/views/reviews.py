import logging

from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from ..models import Review
from ..serializers import ReviewSerializer

logger = logging.getLogger(__name__)


from core.permissions import IsVerifiedInRegion

class ReviewViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsVerifiedInRegion]
    serializer_class = ReviewSerializer

    def get_target_region(self, request):
        if request.method == 'POST':
            order_id = request.data.get('order')
            if order_id:
                from orders.models import Order
                order = Order.objects.filter(id=order_id).first()
                if order:
                    return order.region
        return None

    def get_queryset(self):
        user = self.request.user
        return Review.objects.filter(Q(reviewer=user) | Q(reviewee=user)).order_by('-created_at')

    def perform_create(self, serializer):
        order = serializer.validated_data['order']
        is_no_show = serializer.validated_data.get('is_no_show', False)

        # Validations
        if self.request.user not in [order.buyer, order.seller]:
            raise serializers.ValidationError({"detail": "You can only review your own orders."})

        if is_no_show and order.status != 'cancelled':
            raise serializers.ValidationError({"detail": "No-show reports are only for cancelled orders."})

        if not is_no_show and order.status != 'completed':
            raise serializers.ValidationError({"detail": "Reviews are only for completed orders."})

        reviewee = order.seller if self.request.user == order.buyer else order.buyer

        # Check if already reviewed
        if Review.objects.filter(order=order, reviewer=self.request.user).exists():
            raise serializers.ValidationError({"detail": "You have already reviewed this order."})

        serializer.save(
            reviewer=self.request.user,
            reviewee=reviewee
        )

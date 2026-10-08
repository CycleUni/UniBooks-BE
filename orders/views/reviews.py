import logging

from django.db import IntegrityError, transaction
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
    # Reviews are write-once. The queryset below deliberately includes reviews
    # *about* the caller (so they can read them), and ModelViewSet's default
    # update/destroy on that same queryset let the person being reviewed
    # rewrite the rating or delete the review outright.
    http_method_names = ['get', 'post', 'head', 'options']

    def get_target_region(self, request):
        if request.method == 'POST':
            order_id = request.data.get('order')
            if order_id:
                from django.core.exceptions import ValidationError as DjangoValidationError
                from orders.models import Order
                try:
                    order = Order.objects.filter(id=order_id).first()
                except (DjangoValidationError, ValueError):
                    return None
                if order:
                    return order.region
        return None

    def get_queryset(self):
        user = self.request.user
        return (
            Review.objects.filter(Q(reviewer=user) | Q(reviewee=user))
            .select_related('reviewer', 'reviewee')
            .order_by('-created_at')
        )

    def perform_create(self, serializer):
        order = serializer.validated_data['order']
        is_no_show = serializer.validated_data.get('is_no_show', False)

        # Codes, not English: the review dialog shows whatever comes back,
        # in the user's language only if it is a dictionary key.
        if self.request.user not in [order.buyer, order.seller]:
            raise serializers.ValidationError({"order": "order.errReviewNotYours"})

        if is_no_show and order.status != 'cancelled':
            raise serializers.ValidationError({"order": "order.errNoShowNeedsCancelled"})

        if not is_no_show and order.status != 'completed':
            raise serializers.ValidationError({"order": "order.errReviewNeedsCompleted"})

        reviewee = order.seller if self.request.user == order.buyer else order.buyer

        # Check if already reviewed
        if Review.objects.filter(order=order, reviewer=self.request.user).exists():
            raise serializers.ValidationError({"order": "order.errAlreadyReviewed"})

        try:
            with transaction.atomic():
                serializer.save(
                    reviewer=self.request.user,
                    reviewee=reviewee
                )
        except IntegrityError:
            # A second submit racing past the check above (a double click).
            raise serializers.ValidationError({"order": "order.errAlreadyReviewed"})

from core.region import get_region
import logging

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework import views, status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from listings.models import Listing
from messaging.models import Conversation
from messaging.serializers import ConversationSerializer
from core.permissions import IsVerifiedInRegion

logger = logging.getLogger(__name__)


class ConversationListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsVerifiedInRegion]
    serializer_class = ConversationSerializer

    def get_target_region(self, request):
        if request.method == 'POST':
            listing_id = request.data.get('listing_id')
            if listing_id:
                listing = Listing.objects.filter(id=listing_id).first()
                if listing:
                    return listing.region
        return None

    def get_queryset(self):
        user = self.request.user
        region = get_region(self.request)
        return Conversation.objects.filter(
            Q(buyer=user) | Q(listing__seller=user),
            listing__region=region
        ).exclude(
            Q(buyer=user, buyer_deleted_at__isnull=False) |
            Q(listing__seller=user, seller_deleted_at__isnull=False)
        ).select_related('listing__book', 'listing__seller', 'buyer').order_by('-updated_at').distinct()

    def post(self, request):
        region = get_region(request)

        listing_id = request.data.get('listing_id')
        if not listing_id:
            return Response({"error": "listing_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            listing = Listing.objects.get(region=region, id=listing_id)
            if listing.seller == request.user:
                return Response({"error": "Cannot message yourself"}, status=status.HTTP_400_BAD_REQUEST)

            conv, created = Conversation.objects.get_or_create(
                listing=listing,
                buyer=request.user
            )
            serializer = ConversationSerializer(conv, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        except Listing.DoesNotExist:
            return Response({"error": "Listing not found"}, status=status.HTTP_404_NOT_FOUND)


class ConversationDeleteView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, conversation_id):
        region = get_region(request)
        try:
            conversation = Conversation.objects.select_related('listing').get(listing__region=region, id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = conversation.mark_deleted_by(request.user)
        if result is False:
            return Response(status=status.HTTP_403_FORBIDDEN)
        # 'deleted' = both parties deleted, row is gone; 'hidden' = only one side
        return Response({"status": result}, status=status.HTTP_200_OK)

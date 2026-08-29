import logging

from django.db.models import Q
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.models import AuditEvent
from listings.models import Listing

from ..permissions import IsRegionManager
from ..serializers import AdminListingSerializer

logger = logging.getLogger(__name__)


class AdminListingListView(generics.ListAPIView):
    """GET /api/v1/admin/listings/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminListingSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Listing.objects.select_related('book', 'seller', 'school').order_by('-created_at')
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(book__title__icontains=q)
                | Q(seller__email__icontains=q)
                | Q(school__name__icontains=q)
            )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        condition = self.request.query_params.get('condition')
        if condition:
            qs = qs.filter(condition=condition)
        school = self.request.query_params.get('school')
        if school:
            qs = qs.filter(school_id=school)
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(region_id=region)
        return qs


class AdminListingDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/listings/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminListingSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        qs = Listing.objects.select_related('book', 'seller', 'school').all()
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
        return qs

    def patch(self, request, *args, **kwargs):
        allowed_fields = {'status'}
        extra = set(request.data.keys()) - allowed_fields
        if extra:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'status' not in request.data:
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = request.data['status']
        valid_statuses = dict(Listing.STATUS_CHOICES)
        if new_status not in valid_statuses:
            return Response(
                {"error": {"code": "admin.errInvalidStatus"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        old_status = instance.status
        instance.status = new_status
        instance.save(update_fields=['status'])

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.listing_status_changed',
            meta={'listing_id': str(instance.id), 'old_status': old_status, 'new_status': new_status},
        )

        return Response(AdminListingSerializer(instance).data)

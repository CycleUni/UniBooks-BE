import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.models import AuditEvent
from listings.models import Listing
from listings.utils import delete_listing
from orders.models import ACTIVE_ORDER_STATUSES
from orders.services import PLATFORM_CANCEL_REASON_MIN_LENGTH, platform_cancel_order

from ..permissions import IsRegionManager
from ..serializers import AdminListingSerializer
from accounts.school_codes import admin_school_filter_id

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
                | Q(school__code__iexact=q)
            )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        condition = self.request.query_params.get('condition')
        if condition:
            qs = qs.filter(condition=condition)
        school = self.request.query_params.get('school')
        if school:
            # Admin screens filter by id; a code or name (as the public pages
            # send) is resolved inside the request's region, since the same
            # code names a different school in the other one. Anything else
            # used to reach the id filter and 500 on a non-integer.
            qs = qs.filter(school_id=admin_school_filter_id(self.request, school))
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(region_id=region)
        return qs


class AdminListingDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH / DELETE /api/v1/admin/listings/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminListingSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch', 'delete']

    def get_queryset(self):
        qs = Listing.objects.select_related('book', 'seller', 'school').all()
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
        return qs

    def patch(self, request, *args, **kwargs):
        allowed_fields = {'status', 'admin_locked', 'admin_lock_reason'}
        extra = set(request.data.keys()) - allowed_fields
        if extra:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'status' not in request.data and 'admin_locked' not in request.data:
            return Response(
                {"error": {"code": "admin.errInvalidField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        user = request.user
        updates = []

        if 'status' in request.data:
            new_status = request.data['status']
            valid_statuses = dict(Listing.STATUS_CHOICES)
            if new_status not in valid_statuses:
                return Response(
                    {"error": {"code": "admin.errInvalidStatus"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if new_status != instance.status:
                old_status = instance.status
                instance.status = new_status
                updates.append('status')
                AuditEvent.objects.create(
                    user=user,
                    kind='admin.listing_status_changed',
                    meta={'listing_id': str(instance.id), 'old_status': old_status, 'new_status': new_status},
                )

        # Taking a listing down is what the lock exists for: without it the
        # seller could simply set it back to active. An explicit admin_locked
        # in the same request still wins.
        if 'admin_locked' in request.data:
            new_locked = request.data['admin_locked']
            if not isinstance(new_locked, bool):
                return Response(
                    {"error": {"code": "admin.errInvalidField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif 'status' in updates and instance.status == 'removed':
            new_locked = True
        else:
            new_locked = instance.admin_locked

        if new_locked != instance.admin_locked:
            instance.admin_locked = new_locked
            updates.append('admin_locked')
            if new_locked:
                instance.admin_lock_reason = str(request.data.get('admin_lock_reason', ''))[:255]
                instance.locked_by = user
                instance.locked_at = timezone.now()
            else:
                instance.admin_lock_reason = ''
                instance.locked_by = None
                instance.locked_at = None
            AuditEvent.objects.create(
                user=user,
                kind='admin.listing_locked' if new_locked else 'admin.listing_unlocked',
                meta={'listing_id': str(instance.id)},
            )

        if updates:
            update_fields = ['status'] if 'status' in updates else []
            if 'admin_locked' in updates:
                update_fields.extend(['admin_locked', 'admin_lock_reason', 'locked_by', 'locked_at'])
            instance.save(update_fields=update_fields)

        return Response(AdminListingSerializer(instance).data)

    def delete(self, request, *args, **kwargs):
        instance = self.get_object()
        # The platform may remove any listing, open orders or not: that is
        # how a violation is dealt with. Finished orders keep their snapshot
        # (listings/snapshot.py). Open ones would be left pointing at nothing,
        # so they are cancelled first, exactly as a force-cancel would — which
        # needs the same written reason.
        open_orders = list(instance.orders.filter(status__in=ACTIVE_ORDER_STATUSES))
        reason = (request.data.get('reason') or request.query_params.get('reason') or '').strip()
        if open_orders and len(reason) < PLATFORM_CANCEL_REASON_MIN_LENGTH:
            return Response(
                {"error": {"code": "admin.errInvalidReason"}, "open_orders": len(open_orders)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            for order in open_orders:
                platform_cancel_order(order, reason, request.user)
            self._delete(request, instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _delete(self, request, instance):
        AuditEvent.objects.create(
            user=request.user,
            kind='admin.listing_deleted',
            meta={
                'listing_id': str(instance.id),
                'seller_id': str(instance.seller_id),
                'book_title': instance.book.title,
                'status': instance.status,
                'admin_locked': instance.admin_locked,
                'admin_lock_reason': instance.admin_lock_reason,
            },
        )
        delete_listing(instance)

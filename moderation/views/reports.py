from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from listings.models import Listing
from ..models import Report
from core.region import get_region
from ..serializers import (
    ReportCreateSerializer, ReportSerializer, ReportStatusUpdateSerializer,
)


from core.permissions import IsVerifiedInRegion

class ReportCreateView(generics.CreateAPIView):
    """POST /api/v1/moderation/ submits a report."""
    permission_classes = [IsAuthenticated, IsVerifiedInRegion]
    serializer_class = ReportCreateSerializer

    def get_target_region(self, request):
        if request.method == 'POST':
            listing_id = request.data.get('listing')
            if listing_id:
                listing = Listing.objects.filter(id=listing_id).first()
                if listing:
                    return listing.region
        return None

    def create(self, request, *args, **kwargs):
        listing_id = request.data.get('listing')
        region = get_region(request)
        listing = get_object_or_404(Listing.objects.filter(region=region), pk=listing_id)

        if listing.seller_id == request.user.id:
            return Response(
                {"error": {"code": "moderation.errCannotReportOwnListing"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        already_reported = Report.objects.filter(
            reporter=request.user, listing=listing, status='open'
        ).exists()
        if already_reported:
            return Response(
                {"error": {"code": "moderation.errAlreadyReported"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(reporter=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MyReportsView(generics.ListAPIView):
    """GET /api/v1/moderation/mine/ returns the status of the user's own submitted reports."""
    permission_classes = [IsAuthenticated]
    serializer_class = ReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        region = get_region(self.request)
        return Report.objects.filter(reporter=self.request.user, listing__region=region).order_by('-created_at')


class ReportListView(generics.ListAPIView):
    """GET /api/v1/moderation/ staff-only, lists all reports.

    Defaults to returning all reports (newest first); filter with ?status=open.
    Defaulting to "all" rather than "open" lets staff see the full picture at
    a glance and narrow it down themselves via the query param.
    """
    permission_classes = [IsAdminUser]
    serializer_class = ReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Report.objects.all().order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class ReportActionView(generics.UpdateAPIView):
    """PATCH /api/v1/moderation/<id>/ staff-only, reviews a report.

    Setting status to actioned also takes down the related listing
    (status='removed'); setting it to dismissed only updates the report's
    own status, the listing is unaffected.
    """
    permission_classes = [IsAdminUser]
    serializer_class = ReportStatusUpdateSerializer
    queryset = Report.objects.all()
    lookup_field = 'id'
    http_method_names = ['patch']

    def perform_update(self, serializer):
        report = serializer.save()
        if report.status == 'actioned':
            listing = report.listing
            listing.status = 'removed'
            listing.save(update_fields=['status'])

        from core.models import AuditEvent
        AuditEvent.objects.create(
            user=self.request.user,
            kind='admin.report_actioned',
            meta={
                'report_id': str(report.id),
                'action': report.status,
                'listing_id': str(report.listing_id) if report.status == 'actioned' else None,
            },
        )

    def update(self, request, *args, **kwargs):
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(ReportSerializer(instance).data)

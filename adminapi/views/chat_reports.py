import logging
import time

from django.conf import settings
from rest_framework_simplejwt.backends import TokenBackend
from django.shortcuts import get_object_or_404
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.models import AuditEvent
from moderation.models import ChatReport

from ..permissions import IsRegionManager
from ..serializers import AdminChatReportSerializer

logger = logging.getLogger(__name__)


class AdminChatReportListView(generics.ListAPIView):
    """GET /api/v1/admin/chat-reports/ (read-only list)"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminChatReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = ChatReport.objects.select_related(
            'conversation', 'conversation__listing', 'conversation__listing__book',
            'reporter', 'reported_party'
        ).order_by('-created_at')
        if not self.request.user.is_superuser:
            qs = qs.filter(conversation__listing__region__in=self.request.user.managed_regions.all())
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(conversation__listing__region_id=region)
        return qs


class AdminChatReportDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/chat-reports/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminChatReportSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        qs = ChatReport.objects.select_related(
            'conversation', 'conversation__listing', 'conversation__listing__book',
            'reporter', 'reported_party'
        ).all()
        if not self.request.user.is_superuser:
            qs = qs.filter(conversation__listing__region__in=self.request.user.managed_regions.all())
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
        if new_status not in ('open', 'actioned', 'dismissed'):
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
            kind='admin.chat_report_updated',
            meta={
                'chat_report_id': str(instance.id),
                'old_status': old_status,
                'new_status': new_status,
            },
        )

        return Response(AdminChatReportSerializer(instance).data)


class AdminChatReportTokenView(views.APIView):
    """GET /api/v1/admin/chat-reports/<id>/chat-token/ — issue room-scoped JWT for admin message viewing."""
    permission_classes = [IsAdminUser, IsRegionManager]

    def get(self, request, pk):
        qs = ChatReport.objects.select_related('conversation')
        if not request.user.is_superuser:
            qs = qs.filter(conversation__listing__region__in=request.user.managed_regions.all())
        chat_report = get_object_or_404(qs, pk=pk)
        conv = chat_report.conversation

        # Read through settings like every other view (settings.py is the
        # single audited place env vars are declared); os.getenv bypassed
        # that and the test-time override_settings that comes with it.
        edge_jwt_secret = settings.EDGE_CHAT_JWT_SECRET
        if not edge_jwt_secret:
            return Response(
                {"error": {"code": "admin.chatNotConfigured"}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        app_id = getattr(settings, 'EDGE_CHAT_APP_ID', 'unibooks')
        payload = {
            'user_id': str(request.user.id),
            'room_id': str(conv.id),
            'app_id': app_id,
            'exp': int(time.time()) + 300,
        }
        token = TokenBackend(algorithm="HS256", signing_key=edge_jwt_secret).encode(payload)

        edge_chat_url = settings.EDGE_CHAT_URL.rstrip('/')
        return Response({
            'token': token,
            'edge_chat_url': edge_chat_url,
            'room_id': str(conv.id),
        })

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from ..models import ChatReport
from core.region import get_region
from ..serializers import (
    ChatReportCreateSerializer, ChatReportSerializer, ChatReportStatusUpdateSerializer,
)


class ChatReportCreateView(generics.CreateAPIView):
    """POST /api/v1/moderation/chat-reports/ submits a chat report."""
    permission_classes = [IsAuthenticated]
    serializer_class = ChatReportCreateSerializer

    def create(self, request, *args, **kwargs):
        conversation_id = request.data.get('conversation')
        if not conversation_id:
            return Response(
                {"error": {"code": "invalid.conversationRequired"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        already_pending = ChatReport.objects.filter(
            reporter=request.user,
            conversation_id=conversation_id,
            conversation__listing__region=get_region(request),
            status='open'
        ).exists()
        if already_pending:
            return Response(
                {"error": {"code": "moderation.errAlreadyReported"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(reporter=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MyChatReportsView(generics.ListAPIView):
    """GET /api/v1/moderation/chat-reports/mine/ returns the user's own submitted chat reports."""
    permission_classes = [IsAuthenticated]
    serializer_class = ChatReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        region = get_region(self.request)
        return ChatReport.objects.filter(reporter=self.request.user, conversation__listing__region=region).order_by('-created_at')


class ChatReportListView(generics.ListAPIView):
    """GET /api/v1/moderation/chat-reports/all/ (staff only) lists all chat reports."""
    permission_classes = [IsAdminUser]
    serializer_class = ChatReportSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = ChatReport.objects.select_related(
            'conversation', 'conversation__listing', 'conversation__listing__book',
            'reporter', 'reported_party'
        ).order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class ChatReportActionView(generics.UpdateAPIView):
    """PATCH /api/v1/moderation/chat-reports/<id>/ staff only, reviews a chat report."""
    permission_classes = [IsAdminUser]
    serializer_class = ChatReportStatusUpdateSerializer
    queryset = ChatReport.objects.all()
    lookup_field = 'id'
    http_method_names = ['patch']

    def perform_update(self, serializer):
        report = serializer.save()

        from core.models import AuditEvent
        AuditEvent.objects.create(
            user=self.request.user,
            kind='admin.chat_report_actioned',
            meta={
                'chat_report_id': str(report.id),
                'action': report.status,
                'conversation_id': str(report.conversation_id),
            },
        )

    def update(self, request, *args, **kwargs):
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(ChatReportSerializer(instance).data)


class ChatReportDetailView(generics.RetrieveAPIView):
    """GET /api/v1/moderation/chat-reports/<id>/ staff only, returns full detail."""
    permission_classes = [IsAdminUser]
    serializer_class = ChatReportSerializer
    queryset = ChatReport.objects.select_related(
        'conversation', 'conversation__listing', 'conversation__listing__book',
        'reporter', 'reported_party',
    ).all()
    lookup_field = 'id'

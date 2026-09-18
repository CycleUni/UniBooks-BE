from django.db.models import Q
from rest_framework import generics, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import SchoolRequest
from adminapi.pagination import AdminPagination
from core.models import AuditEvent

from ..permissions import IsRegionManager
from ..serializers import AdminSchoolRequestSerializer

# Long enough for "added as <domain>, merged with #12", short enough that the
# field cannot be used as a document store.
ADMIN_NOTE_MAX_LENGTH = 2000


def _scoped(qs, user):
    """Staff see the regions they manage; superusers see every region."""
    if user.is_superuser:
        return qs
    return qs.filter(region__in=user.managed_regions.all())


class AdminSchoolRequestListView(generics.ListAPIView):
    """GET /api/v1/admin/school-requests/

    ?status=pending|added|rejected, ?q= (school name, website, reporter's
    email or the address they typed), ?region=, ?page=.
    """
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminSchoolRequestSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = _scoped(SchoolRequest.objects.select_related('user'), self.request.user).order_by('-created_at')
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(school_name__icontains=q)
                | Q(school_website__icontains=q)
                | Q(user__email__icontains=q)
                | Q(edu_email__icontains=q)
            )
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(region_id=region)
        return qs


class AdminSchoolRequestDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/school-requests/<id>/

    PATCH takes `status` and/or `admin_note` and nothing else. Marking a
    request "added" does not create the School — staff add it on the schools
    page, where the email domain is entered and checked; this only records
    the outcome for the queue.
    """
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminSchoolRequestSerializer
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        return _scoped(SchoolRequest.objects.select_related('user'), self.request.user)

    def patch(self, request, *args, **kwargs):
        allowed_fields = {'status', 'admin_note'}
        keys = set(request.data.keys())
        if keys - allowed_fields:
            return Response({"error": {"code": "admin.errForbiddenField"}}, status=status.HTTP_400_BAD_REQUEST)
        if not keys:
            return Response({"error": {"code": "admin.errInvalidField"}}, status=status.HTTP_400_BAD_REQUEST)

        valid_statuses = {choice for choice, _label in SchoolRequest.STATUS_CHOICES}
        new_status = request.data.get('status')
        if 'status' in keys and new_status not in valid_statuses:
            return Response({"error": {"code": "admin.errInvalidStatus"}}, status=status.HTTP_400_BAD_REQUEST)

        note = request.data.get('admin_note')
        if 'admin_note' in keys:
            if note is None:
                note = ''
            if not isinstance(note, str) or len(note) > ADMIN_NOTE_MAX_LENGTH:
                return Response({"error": {"code": "admin.errInvalidField"}}, status=status.HTTP_400_BAD_REQUEST)

        # Looked up after validation but before any write: get_object() is
        # where both the region-scoped queryset and IsRegionManager's object
        # check run, so another region's request 404s here.
        instance = self.get_object()
        old_status = instance.status
        update_fields = ['updated_at']
        if 'status' in keys:
            instance.status = new_status
            update_fields.append('status')
        if 'admin_note' in keys:
            instance.admin_note = note
            update_fields.append('admin_note')
        instance.save(update_fields=update_fields)

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.school_request_updated',
            meta={
                'school_request_id': instance.id,
                'old_status': old_status,
                'new_status': instance.status,
                'note_changed': 'admin_note' in keys,
            },
        )

        return Response(AdminSchoolRequestSerializer(instance).data)

import logging
from django.db.models import Count, Q

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import School
from accounts.views import invalidate_home_static_cache

from ..serializers import AdminSchoolSerializer

logger = logging.getLogger(__name__)


class AdminSchoolListView(generics.ListCreateAPIView):
    """GET / POST /api/v1/admin/schools/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminSchoolSerializer
    pagination_class = PageNumberPagination
    
    def get_queryset(self):
        qs = School.objects.annotate(user_count_annotated=Count('users')).order_by('id')
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(email_domain__icontains=q)
            )
        return qs

    def perform_create(self, serializer):
        serializer.save()
        invalidate_home_static_cache()


class AdminSchoolDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE /api/v1/admin/schools/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminSchoolSerializer
    queryset = School.objects.all()
    http_method_names = ['get', 'patch', 'delete']

    def perform_update(self, serializer):
        serializer.save()
        invalidate_home_static_cache()

    def perform_destroy(self, instance):
        # A school with accounts attached cannot be removed: users would silently
        # lose their school (FK is SET_NULL) and every listing under it would be
        # cascade-deleted. Admins must reassign or remove the accounts first.
        user_count = instance.users.count()
        if user_count:
            raise ValidationError({
                'detail': _('This school still has %(count)d account(s) and cannot be deleted. '
                            'Reassign or remove those accounts first.') % {'count': user_count}
            })
        instance.delete()
        invalidate_home_static_cache()


class AdminSchoolBulkImportView(views.APIView):
    """POST /api/v1/admin/schools/bulk/"""
    permission_classes = [IsAdminUser]

    def post(self, request):
        action = request.data.get('action') # 'preview' or 'apply'
        items = request.data.get('items', [])

        if not isinstance(items, list):
            return Response({"error": "Items must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        new_items = []
        unchanged_items = []
        modified_items = []

        # Build map of existing schools by email_domain
        existing_schools = {s.email_domain: s for s in School.objects.all()}

        for idx, item in enumerate(items):
            domain = item.get('email_domain')
            if not domain:
                continue

            existing = existing_schools.get(domain)
            if not existing:
                new_items.append(item)
                if action == 'apply':
                    School.objects.create(
                        name=item.get('name', ''),
                        email_domain=domain,
                        translations=item.get('translations', {})
                    )
            else:
                # Check for changes
                name = item.get('name', existing.name)
                translations = item.get('translations', existing.translations)

                is_changed = existing.name != name or existing.translations != translations
                if is_changed:
                    modified_items.append({
                        'old': AdminSchoolSerializer(existing).data,
                        'new': item
                    })
                    if action == 'apply':
                        existing.name = name
                        existing.translations = translations
                        existing.save()
                else:
                    unchanged_items.append(item)

        if action == 'apply' and (new_items or modified_items):
            invalidate_home_static_cache()

        return Response({
            "new": new_items,
            "modified": modified_items,
            "unchanged": unchanged_items
        })

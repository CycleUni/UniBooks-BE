import logging
from django.db.models import Count, Q

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework import generics, status, views
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from adminapi.pagination import AdminPagination
from accounts.models import School
from accounts.views import invalidate_home_static_cache

from ..permissions import IsRegionManager
from ..serializers import AdminSchoolSerializer

logger = logging.getLogger(__name__)


class AdminSchoolListView(generics.ListCreateAPIView):
    """GET / POST /api/v1/admin/schools/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminSchoolSerializer
    pagination_class = AdminPagination
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        from core.i18n import resolve_language
        context['lang'] = resolve_language(self.request)
        return context

    def get_queryset(self):
        qs = School.objects.annotate(user_count_annotated=Count('verifications')).order_by('id')
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(email_domain__icontains=q)
            )
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(region_id=region)
        return qs

    def perform_create(self, serializer):
        region = serializer.validated_data.get('region')
        if not self.request.user.is_superuser and region not in self.request.user.managed_regions.all():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied({"error": {"code": "admin.errRegionForbidden"}})
        serializer.save()
        invalidate_home_static_cache()


class AdminSchoolDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE /api/v1/admin/schools/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminSchoolSerializer
    http_method_names = ['get', 'patch', 'delete']

    def get_serializer_context(self):
        context = super().get_serializer_context()
        from core.i18n import resolve_language
        context['lang'] = resolve_language(self.request)
        return context

    def get_queryset(self):
        qs = School.objects.all()
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
        return qs

    def perform_update(self, serializer):
        region = serializer.validated_data.get('region', serializer.instance.region)
        if not self.request.user.is_superuser and region not in self.request.user.managed_regions.all():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied({"error": {"code": "admin.errRegionForbidden"}})
        serializer.save()
        invalidate_home_static_cache()

    def perform_destroy(self, instance):
        # A school with accounts attached cannot be removed: users would silently
        # lose their school (FK is SET_NULL) and every listing under it would be
        # cascade-deleted. Admins must reassign or remove the accounts first.
        user_count = instance.verifications.count()
        if user_count:
            raise ValidationError({
                'detail': _('This school still has %(count)d account(s) and cannot be deleted. '
                            'Reassign or remove those accounts first.') % {'count': user_count}
            })
        instance.delete()
        invalidate_home_static_cache()


class AdminSchoolBulkImportView(views.APIView):
    """POST /api/v1/admin/schools/bulk/"""
    permission_classes = [IsAdminUser, IsRegionManager]

    def post(self, request):
        action = request.data.get('action') # 'preview' or 'apply'
        items = request.data.get('items', [])

        if not isinstance(items, list):
            return Response({"error": "Items must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        new_items = []
        unchanged_items = []
        modified_items = []
        forbidden_items = []

        # Build map of existing schools by email_domain
        existing_schools = {s.email_domain: s for s in School.objects.all()}
        current_region = (request.query_params.get('region') or '').upper()

        if not current_region:
            return Response({"error": "Missing region parameter"}, status=status.HTTP_400_BAD_REQUEST)

        managed_region_codes = set(request.user.managed_regions.values_list('code', flat=True)) if not request.user.is_superuser else None

        if not request.user.is_superuser and current_region not in managed_region_codes:
            return Response({"error": {"code": "admin.errRegionForbidden"}}, status=status.HTTP_403_FORBIDDEN)

        valid_count = 0
        for idx, item in enumerate(items):
            domain = item.get('email_domain')
            if not domain:
                continue
            
            valid_count += 1
            region_code = item.get('region')
            
            # (c) Item has no region -> assumed to be current_region
            target_region = region_code if region_code else current_region
            
            existing = existing_schools.get(domain)

            # (b) & (e) Item's region doesn't match current region, or existing school belongs to another region -> forbidden
            if target_region != current_region or (existing and existing.region_id != current_region):
                forbidden_items.append(item)
                continue

            if not existing:
                new_items.append(item)
                if action == 'apply':
                    School.objects.create(
                        name=item.get('name', ''),
                        email_domain=domain,
                        region_id=current_region,
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

        if valid_count > 0 and len(forbidden_items) == valid_count:
            return Response({"error": {"code": "admin.errAllRegionsForbidden"}}, status=status.HTTP_403_FORBIDDEN)

        if action == 'apply' and (new_items or modified_items):
            invalidate_home_static_cache()

        return Response({
            "new": new_items,
            "modified": modified_items,
            "unchanged": unchanged_items,
            "forbidden": forbidden_items
        })

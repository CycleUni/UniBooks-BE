import logging

from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.views import invalidate_home_static_cache
from core.models import Category

from ..permissions import IsRegionManager
from ..serializers import AdminCategorySerializer

logger = logging.getLogger(__name__)


class AdminCategoryListView(generics.ListCreateAPIView):
    serializer_class = AdminCategorySerializer
    permission_classes = [IsAdminUser, IsRegionManager]
    pagination_class = PageNumberPagination

    def get_queryset(self):
        qs = Category.objects.all().order_by('sort_order', 'id')
        if not self.request.user.is_superuser:
            qs = qs.filter(region__in=self.request.user.managed_regions.all())
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


class AdminCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminCategorySerializer
    permission_classes = [IsAdminUser, IsRegionManager]
    http_method_names = ['get', 'patch', 'delete']

    def get_queryset(self):
        qs = Category.objects.all()
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
        instance.delete()
        invalidate_home_static_cache()


class AdminCategoryBulkImportView(views.APIView):
    """POST /api/v1/admin/categories/bulk/"""
    permission_classes = [IsAdminUser, IsRegionManager]

    def post(self, request):
        action = request.data.get('action') # 'preview' or 'apply'
        items = request.data.get('items', [])

        if not isinstance(items, list):
            return Response({"error": "Items must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        new_items = []
        unchanged_items = []
        modified_items = []

        existing_cats = {(c.slug, c.region_id): c for c in Category.objects.all()}
        current_region = (request.query_params.get('region') or '').upper()

        if not current_region:
            return Response({"error": "Missing region parameter"}, status=status.HTTP_400_BAD_REQUEST)

        managed_region_codes = set(request.user.managed_regions.values_list('code', flat=True)) if not request.user.is_superuser else None

        if not request.user.is_superuser and current_region not in managed_region_codes:
            return Response({"error": {"code": "admin.errRegionForbidden"}}, status=status.HTTP_403_FORBIDDEN)

        valid_count = 0
        forbidden_items = []

        for item in items:
            slug = item.get('slug')
            if not slug:
                continue

            valid_count += 1
            region_code = item.get('region')
            
            # (c) Item has no region -> assumed to be current_region
            target_region = region_code if region_code else current_region
            
            existing = existing_cats.get((slug, current_region))
            # Also check if it exists in another region
            existing_any_region = next((c for (s, r), c in existing_cats.items() if s == slug and r != current_region), None)

            # (b) & (e) Item's region doesn't match current region -> forbidden
            if target_region != current_region or (existing_any_region and not existing):
                forbidden_items.append(item)
                continue

            if not existing:
                new_items.append(item)
                if action == 'apply':
                    Category.objects.create(
                        slug=slug,
                        region_id=current_region,
                        title=item.get('title', ''),
                        description=item.get('description', ''),
                        sort_order=item.get('sort_order', 0),
                        is_active=item.get('is_active', True),
                        translations=item.get('translations', {})
                    )
            else:
                title = item.get('title', existing.title)
                description = item.get('description', existing.description)
                sort_order = item.get('sort_order', existing.sort_order)
                is_active = item.get('is_active', existing.is_active)
                translations = item.get('translations', existing.translations)

                is_changed = (
                    existing.title != title or
                    existing.description != description or
                    existing.sort_order != sort_order or
                    existing.is_active != is_active or
                    existing.translations != translations
                )

                if is_changed:
                    modified_items.append({
                        'old': AdminCategorySerializer(existing).data,
                        'new': item
                    })
                    if action == 'apply':
                        existing.title = title
                        existing.description = description
                        existing.sort_order = sort_order
                        existing.is_active = is_active
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

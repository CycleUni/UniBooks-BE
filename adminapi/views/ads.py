import logging

from django.db.models import Q
from django.core.files.storage import default_storage
from rest_framework import generics
from rest_framework.permissions import IsAdminUser

from ads.models import Advertiser, Ad
from adminapi.pagination import AdminPagination
from adminapi.serializers import AdminAdvertiserSerializer, AdminAdSerializer
from core.uploads import storage_key_from_url
from core.cache import bump_cache_version

from ..permissions import IsRegionManager

logger = logging.getLogger(__name__)

class AdminAdvertiserListView(generics.ListCreateAPIView):
    """GET /api/v1/admin/advertisers/
    POST /api/v1/admin/advertisers/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminAdvertiserSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = Advertiser.objects.all().order_by('-created_at')
        if not self.request.user.is_superuser:
            qs = qs.filter(Q(all_regions=True) | Q(regions__in=self.request.user.managed_regions.all())).distinct()
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(company_name__icontains=q) |
                Q(contact_email__icontains=q)
            )
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(Q(all_regions=True) | Q(regions__code=region)).distinct()
        return qs

    def perform_create(self, serializer):
        if not self.request.user.is_superuser:
            regions = serializer.validated_data.get('regions', [])
            managed = self.request.user.managed_regions.all()
            if serializer.validated_data.get('all_regions') or any(r not in managed for r in regions):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied({"error": {"code": "admin.errRegionForbidden"}})
        super().perform_create(serializer)
        bump_cache_version('ads')

class AdminAdvertiserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/v1/admin/advertisers/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminAdvertiserSerializer
    
    def get_queryset(self):
        qs = Advertiser.objects.all()
        if not self.request.user.is_superuser:
            qs = qs.filter(Q(all_regions=True) | Q(regions__in=self.request.user.managed_regions.all())).distinct()
        return qs

    def perform_update(self, serializer):
        if not self.request.user.is_superuser:
            regions = serializer.validated_data.get('regions', serializer.instance.regions.all())
            all_regions = serializer.validated_data.get('all_regions', serializer.instance.all_regions)
            managed = self.request.user.managed_regions.all()
            if all_regions or any(r not in managed for r in regions):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied({"error": {"code": "admin.errRegionForbidden"}})
        super().perform_update(serializer)
        bump_cache_version('ads')

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        bump_cache_version('ads')

class AdminAdListView(generics.ListCreateAPIView):
    """GET /api/v1/admin/ads/
    POST /api/v1/admin/ads/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminAdSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = Ad.objects.select_related('advertiser').all().order_by('-created_at')
        if not self.request.user.is_superuser:
            qs = qs.filter(Q(advertiser__all_regions=True) | Q(advertiser__regions__in=self.request.user.managed_regions.all())).distinct()
        advertiser_id = self.request.query_params.get('advertiser_id')
        if advertiser_id:
            qs = qs.filter(advertiser_id=advertiser_id)
        
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(advertiser__company_name__icontains=q)
            )
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        bump_cache_version('ads')

class AdminAdDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/v1/admin/ads/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminAdSerializer
    
    def get_queryset(self):
        qs = Ad.objects.select_related('advertiser').all()
        if not self.request.user.is_superuser:
            qs = qs.filter(Q(advertiser__all_regions=True) | Q(advertiser__regions__in=self.request.user.managed_regions.all())).distinct()
        return qs

    def perform_destroy(self, instance):
        image_url = instance.image_url
        super().perform_destroy(instance)
        bump_cache_version('ads')
        if image_url:
            key = storage_key_from_url(image_url, self.request)
            if key:
                try:
                    if default_storage.exists(key):
                        default_storage.delete(key)
                except Exception:
                    logger.exception("Failed to delete ad image from storage: %s", key)

    def perform_update(self, serializer):
        old_image_url = self.get_object().image_url
        super().perform_update(serializer)
        bump_cache_version('ads')
        new_image_url = serializer.instance.image_url
        
        if old_image_url and old_image_url != new_image_url:
            key = storage_key_from_url(old_image_url, self.request)
            if key:
                try:
                    if default_storage.exists(key):
                        default_storage.delete(key)
                except Exception:
                    logger.exception("Failed to delete old ad image from storage on update: %s", key)

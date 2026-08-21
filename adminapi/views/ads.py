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

logger = logging.getLogger(__name__)

class AdminAdvertiserListView(generics.ListCreateAPIView):
    """GET /api/v1/admin/advertisers/
    POST /api/v1/admin/advertisers/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminAdvertiserSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = Advertiser.objects.all().order_by('-created_at')
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(company_name__icontains=q) |
                Q(contact_email__icontains=q)
            )
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        bump_cache_version('ads')

class AdminAdvertiserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/v1/admin/advertisers/<id>/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminAdvertiserSerializer
    queryset = Advertiser.objects.all()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        bump_cache_version('ads')

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        bump_cache_version('ads')

class AdminAdListView(generics.ListCreateAPIView):
    """GET /api/v1/admin/ads/
    POST /api/v1/admin/ads/"""
    permission_classes = [IsAdminUser]
    serializer_class = AdminAdSerializer
    pagination_class = AdminPagination

    def get_queryset(self):
        qs = Ad.objects.select_related('advertiser').all().order_by('-created_at')
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
    permission_classes = [IsAdminUser]
    serializer_class = AdminAdSerializer
    queryset = Ad.objects.all()

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

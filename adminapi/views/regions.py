from rest_framework import generics
from rest_framework.permissions import BasePermission
from core.models import Region, Currency
from adminapi.serializers import AdminRegionSerializer, AdminCurrencySerializer

class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)

class AdminCurrencyListView(generics.ListCreateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer

class AdminCurrencyDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer

class AdminRegionListView(generics.ListCreateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Region.objects.all()
    serializer_class = AdminRegionSerializer

class AdminRegionDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Region.objects.all()
    serializer_class = AdminRegionSerializer

from rest_framework import generics
from rest_framework.permissions import BasePermission
from core.models import Region, Currency
from adminapi.serializers import AdminRegionSerializer, AdminCurrencySerializer

class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class LocalizedSerializerContext:
    """Puts the request's language into the serializer context.

    AdminRegionSerializer.get_display_name falls back to the canonical English
    name when `lang` is absent, so without this the region list read 'Taiwan'
    and 'Hong Kong' in a Chinese interface — the serializer was right and the
    context was empty. AdminSchoolListView does the same thing, which is why
    school names localised and region names did not.
    """

    def get_serializer_context(self):
        context = super().get_serializer_context()
        from core.i18n import resolve_language
        context['lang'] = resolve_language(self.request)
        return context

class AdminCurrencyListView(generics.ListCreateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer

class AdminCurrencyDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer

class AdminRegionListView(LocalizedSerializerContext, generics.ListCreateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Region.objects.all()
    serializer_class = AdminRegionSerializer

class AdminRegionDetailView(LocalizedSerializerContext, generics.RetrieveUpdateAPIView):
    permission_classes = [IsSuperuser]
    queryset = Region.objects.all()
    serializer_class = AdminRegionSerializer

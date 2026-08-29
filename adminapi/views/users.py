import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from accounts.models import School, User
from core.i18n import resolve_language
from core.models import AuditEvent

from ..permissions import IsRegionManager
from ..serializers import AdminUserSerializer

logger = logging.getLogger(__name__)

# is_staff/is_superuser/password/groups/user_permissions must never be settable
# through this API — granting admin rights stays Django-admin-only (security
# decision, not an oversight). Reject explicitly rather than silently dropping,
# so the frontend doesn't think the write succeeded.
FORBIDDEN_USER_FIELDS = {'is_staff', 'is_superuser', 'password', 'groups', 'user_permissions'}


class AdminUserListView(generics.ListAPIView):
    """GET /api/v1/admin/users/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminUserSerializer
    pagination_class = PageNumberPagination

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['lang'] = resolve_language(self.request)
        return context

    def get_queryset(self):
        qs = User.objects.prefetch_related('region_verifications__school').order_by('-created_at')
        if not self.request.user.is_superuser:
            qs = qs.filter(
                Q(region_verifications__region__in=self.request.user.managed_regions.all()) |
                Q(region_verifications__isnull=True)
            ).distinct()
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(region_verifications__edu_email__icontains=q)
            )
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        school = self.request.query_params.get('school')
        if school:
            qs = qs.filter(region_verifications__school_id=school)
        # Uppercased: Region.code is 'TW'/'HK', but the frontend spells the
        # region the way the URL does (lowercase) and ApiUrlInterceptor
        # appends it to every request — so an unnormalized comparison made
        # every admin list come back empty.
        region = (self.request.query_params.get('region') or '').upper()
        if region:
            qs = qs.filter(
                Q(region_verifications__region_id=region) |
                Q(region_verifications__isnull=True)
            ).distinct()
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['lang'] = resolve_language(self.request)
        return context


class AdminUserDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/v1/admin/users/<id>/"""
    permission_classes = [IsAdminUser, IsRegionManager]
    serializer_class = AdminUserSerializer
    lookup_field = 'pk'
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        qs = User.objects.prefetch_related('region_verifications__school').all()
        if not self.request.user.is_superuser:
            qs = qs.filter(
                Q(region_verifications__region__in=self.request.user.managed_regions.all()) |
                Q(region_verifications__isnull=True)
            ).distinct()
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['lang'] = resolve_language(self.request)
        return context

    def patch(self, request, *args, **kwargs):
        forbidden = FORBIDDEN_USER_FIELDS & set(request.data.keys())
        if forbidden:
            return Response(
                {"error": {"code": "admin.errForbiddenField"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        changes = {}

        # Prevent admin from disabling their own account
        if request.data.get('is_active') is False and instance.id == request.user.id:
            return Response(
                {"error": {"code": "admin.errSelfDisable"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'is_active' in request.data:
            value = request.data['is_active']
            if not isinstance(value, bool):
                return Response(
                    {"error": {"code": "admin.errInvalidField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Prevent disabling superuser or staff accounts
            if not value and (instance.is_superuser or instance.is_staff):
                return Response(
                    {"error": {"code": "admin.errForbiddenField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if instance.is_active != value:
                instance.is_active = value
                changes['is_active'] = value

        if 'school' in request.data:
            school_id = request.data['school']
            new_school = None
            if school_id is not None:
                try:
                    new_school = School.objects.get(pk=school_id)
                except (School.DoesNotExist, ValueError, TypeError):
                    return Response(
                        {"error": {"code": "admin.errInvalidField"}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            new_school_id = new_school.id if new_school else None
            
            region_code = request.data.get('region')
            if not region_code:
                return Response({"error": {"code": "admin.errMissingRegion"}}, status=status.HTTP_400_BAD_REQUEST)
            if not request.user.is_superuser and not request.user.managed_regions.filter(code=region_code).exists():
                return Response({"error": {"code": "admin.errRegionForbidden"}}, status=status.HTTP_403_FORBIDDEN)
                
            from accounts.models import RegionVerification
            from django.db import IntegrityError
            try:
                verification, created = RegionVerification.objects.update_or_create(
                    user=instance,
                    region_id=region_code,
                    defaults={'is_active': True}
                )
                if created:
                    verification.edu_email = None
                    verification.is_manual_verification = True
                    verification.save(update_fields=['edu_email', 'is_manual_verification'])
            except IntegrityError:
                return Response({"error": {"code": "admin.errDatabaseIntegrity"}}, status=status.HTTP_400_BAD_REQUEST)

            if verification.school_id != new_school_id:
                verification.school = new_school
                verification.save(update_fields=['school'])
                changes['school'] = new_school_id

        if 'verified' in request.data:
            value = request.data['verified']
            if not isinstance(value, bool):
                return Response(
                    {"error": {"code": "admin.errInvalidField"}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            region_code = request.data.get('region')
            if not region_code:
                return Response({"error": {"code": "admin.errMissingRegion"}}, status=status.HTTP_400_BAD_REQUEST)
            if not request.user.is_superuser and not request.user.managed_regions.filter(code=region_code).exists():
                return Response({"error": {"code": "admin.errRegionForbidden"}}, status=status.HTTP_403_FORBIDDEN)
                
            from accounts.models import RegionVerification
            from django.db import IntegrityError
            try:
                verification, created = RegionVerification.objects.update_or_create(
                    user=instance,
                    region_id=region_code,
                    defaults={'is_active': True}
                )
                if created:
                    verification.edu_email = None
                    verification.is_manual_verification = True
                    verification.save(update_fields=['edu_email', 'is_manual_verification'])
            except IntegrityError:
                return Response({"error": {"code": "admin.errDatabaseIntegrity"}}, status=status.HTTP_400_BAD_REQUEST)

            if verification:
                if value and not verification.verified_at:
                    verification.verified_at = timezone.now()
                    verification.save(update_fields=['verified_at'])
                    changes['verified_at'] = verification.verified_at.isoformat()
                elif not value and verification.verified_at:
                    verification.verified_at = None
                    verification.save(update_fields=['verified_at'])
                    changes['verified_at'] = None

        instance.save()

        AuditEvent.objects.create(
            user=request.user,
            kind='admin.user_updated',
            meta={'target_user_id': instance.id, 'changes': changes},
        )

        return Response(AdminUserSerializer(instance, context=self.get_serializer_context()).data)

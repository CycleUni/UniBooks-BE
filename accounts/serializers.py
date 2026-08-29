from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from core.i18n import DEFAULT_LANGUAGE, resolve_language
from core.region import get_region

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    is_verified = serializers.SerializerMethodField()
    regions = serializers.SerializerMethodField()
    verifications = serializers.SerializerMethodField()

    def _verification(self, obj):
        rows = [v for v in obj.region_verifications.all() if v.is_active and v.verified_at]
        request = self.context.get('request')
        region = get_region(request) if request else None
        if region:
            for v in rows:
                if v.region_id == region.pk:
                    return v
            return None
        return sorted(rows, key=lambda v: v.region_id)[0] if rows else None

    def get_is_verified(self, obj):
        return self._verification(obj) is not None

    def get_regions(self, obj):
        return sorted([v.region_id for v in obj.region_verifications.all() if v.is_active and v.verified_at])

    def get_verifications(self, obj):
        request = self.context.get('request')
        lang = resolve_language(request) if request else DEFAULT_LANGUAGE
        return [
            {
                'region': v.region_id,
                'school': v.school_id,
                'school_name': v.school.localized_name(lang) if v.school else '',
                'edu_email': v.edu_email,
                'verified_at': v.verified_at,
            }
            for v in sorted((v for v in obj.region_verifications.all() if v.is_active and v.verified_at), key=lambda x: x.region_id)
        ]

    school_name = serializers.SerializerMethodField()

    def get_school_name(self, obj):
        v = self._verification(obj)
        if not v or not v.school:
            return ''
        request = self.context.get('request')
        lang = resolve_language(request) if request else DEFAULT_LANGUAGE
        return v.school.localized_name(lang)

    has_password = serializers.SerializerMethodField()

    def get_has_password(self, obj):
        return obj.has_usable_password()

    is_google_linked = serializers.SerializerMethodField()

    def get_is_google_linked(self, obj):
        return obj.socialaccount_set.filter(provider='google').exists()

    # The admin UI needs to know which regions this staff member may act on:
    # without it the region filter falls back to offering every region, so a
    # Taiwan-only manager is shown a Hong Kong option that can only ever
    # return an empty list. Empty for non-staff, and superusers are
    # unrestricted anyway (is_superuser is already exposed).
    managed_regions = serializers.SerializerMethodField()

    def get_managed_regions(self, obj):
        if not obj.is_staff:
            return []
        return sorted(obj.managed_regions.values_list('code', flat=True))

    edu_email = serializers.SerializerMethodField()
    school = serializers.SerializerMethodField()
    verified_at = serializers.SerializerMethodField()

    def get_edu_email(self, obj):
        v = self._verification(obj)
        return v.edu_email if v else None
    
    def get_school(self, obj):
        v = self._verification(obj)
        return v.school_id if v else None
    
    def get_verified_at(self, obj):
        v = self._verification(obj)
        return v.verified_at if v else None

    class Meta:
        model = User
        fields = ('id', 'email', 'edu_email', 'first_name', 'last_name', 'display_name', 'school', 'school_name', 'is_active', 'is_verified', 'verified_at', 'average_rating', 'review_count', 'no_show_count', 'has_password', 'is_google_linked', 'avatar_url', 'last_seen_bought_orders_at', 'last_seen_sold_orders_at', 'is_staff', 'is_superuser', 'managed_regions', 'regions', 'verifications')
        read_only_fields = ('id', 'edu_email', 'display_name', 'school', 'school_name', 'is_active', 'is_verified', 'verified_at', 'average_rating', 'review_count', 'no_show_count', 'has_password', 'is_google_linked', 'avatar_url', 'last_seen_bought_orders_at', 'last_seen_sold_orders_at', 'is_staff', 'is_superuser', 'managed_regions', 'regions', 'verifications')


class PublicUserProfileSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()
    is_verified = serializers.SerializerMethodField()

    def _verification(self, obj):
        """The seller's verification in the region being browsed.

        Scoped to the request's region, not `.first()`: a seller verified in
        both markets would otherwise have an arbitrary one of their schools
        shown on their public profile, and the badge would claim verification
        in a region where they may hold none.
        """
        rows = [v for v in obj.region_verifications.all() if v.is_active and v.verified_at]
        request = self.context.get('request')
        region = get_region(request) if request else None
        if region:
            for v in rows:
                if v.region_id == region.pk:
                    return v
            return None
        return sorted(rows, key=lambda v: v.region_id)[0] if rows else None

    def get_school_name(self, obj):
        v = self._verification(obj)
        if not v or not v.school:
            return ''
        request = self.context.get('request')
        lang = resolve_language(request) if request else DEFAULT_LANGUAGE
        return v.school.localized_name(lang)

    def get_is_verified(self, obj):
        # Declared as a SerializerMethodField when User.is_verified() was
        # removed for per-region verification, but the method was never added
        # — every public seller profile raised AttributeError and 500'd.
        return self._verification(obj) is not None

    class Meta:
        model = User
        fields = ('id', 'first_name', 'last_name', 'display_name', 'school_name', 'created_at', 'is_verified', 'average_rating', 'review_count', 'no_show_count', 'avatar_url')
        read_only_fields = fields


from rest_framework.validators import UniqueValidator

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    # iexact: Django's own normalize_email only lowercases the domain part,
    # not the local part, so an exact-match uniqueness check would let
    # "User@x.com" and "user@x.com" both register — email lookups elsewhere
    # (login, forgot-password) are case-insensitive, matching normal user
    # expectations, so uniqueness must be enforced the same way or two
    # "different" accounts could exist that no login/reset flow can tell apart.
    email = serializers.EmailField(
        validators=[UniqueValidator(queryset=User.objects.all(), lookup='iexact', message='acct.errEmailTaken')]
    )

    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name', 'password')

    def validate_password(self, value):
        # Same AUTH_PASSWORD_VALIDATORS (min length, common-password check,
        # etc.) already enforced on password reset/change — registration
        # never ran them, so a one-character password sailed straight
        # through this serializer's plain CharField.
        try:
            django_validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def create(self, validated_data):
        # Inactive until they click the verification link mailed on
        # registration (see accounts.views.RegisterView) — LoginView already
        # rejects inactive accounts with auth.errAccountDisabled. Google
        # sign-in is exempt: it creates users via a separate path
        # (GoogleLoginView) that never sets is_active=False, since Google
        # has already verified that email address.
        user = User.objects.create_user(
            # Lowercase the whole address (not just the domain, which is all
            # Django's own normalize_email does) so it's stored the same way
            # login/forgot-password will look it up — see the UniqueValidator
            # comment above for why leaving the local part as-typed causes
            # silent lookup mismatches later.
            email=validated_data['email'].lower(),
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=validated_data['password'],
            is_active=False,
        )
        return user

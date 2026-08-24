from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework_simplejwt.tokens import RefreshToken

from core.i18n import DEFAULT_LANGUAGE, resolve_language

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()

    def get_school_name(self, obj):
        if not obj.school:
            return ''
        request = self.context.get('request')
        lang = resolve_language(request) if request else DEFAULT_LANGUAGE
        return obj.school.localized_name(lang)

    has_password = serializers.SerializerMethodField()

    def get_has_password(self, obj):
        return obj.has_usable_password()

    is_google_linked = serializers.SerializerMethodField()

    def get_is_google_linked(self, obj):
        return obj.socialaccount_set.filter(provider='google').exists()

    class Meta:
        model = User
        fields = ('id', 'email', 'edu_email', 'first_name', 'last_name', 'display_name', 'school', 'school_name', 'is_active', 'verified_at', 'average_rating', 'review_count', 'no_show_count', 'has_password', 'is_google_linked', 'avatar_url', 'last_seen_bought_orders_at', 'last_seen_sold_orders_at', 'is_staff', 'is_superuser')
        read_only_fields = ('id', 'edu_email', 'display_name', 'school', 'school_name', 'is_active', 'verified_at', 'average_rating', 'review_count', 'no_show_count', 'has_password', 'is_google_linked', 'avatar_url', 'last_seen_bought_orders_at', 'last_seen_sold_orders_at', 'is_staff', 'is_superuser')


class PublicUserProfileSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()
    is_verified = serializers.BooleanField(read_only=True)

    def get_school_name(self, obj):
        if not obj.school:
            return ''
        request = self.context.get('request')
        lang = resolve_language(request) if request else DEFAULT_LANGUAGE
        return obj.school.localized_name(lang)

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

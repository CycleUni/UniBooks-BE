"""Read-only serializers backing the Angular admin UI (users / listings / orders).

These are deliberately manual/read-only: writes for each resource are handled
by explicit, narrowly-scoped logic in views.py rather than generic
ModelSerializer.update(), so we can enforce field allow-lists (e.g. never
letting is_staff/is_superuser/password through) precisely per-endpoint.
"""
from rest_framework import serializers

from accounts.models import User, School
from core.models import Category, Region, Currency, Language
from listings.models import Listing
from moderation.models import ChatReport
from orders.models import Order
from ads.models import Advertiser, Ad
from ads.utils import promote_tmp_ad_photos
from rest_framework.exceptions import ValidationError
from core.uploads import storage_key_from_url


class AdminAdvertiserSerializer(serializers.ModelSerializer):
    class Meta:
        model = Advertiser
        fields = '__all__'


class AdminAdSerializer(serializers.ModelSerializer):
    advertiser_name = serializers.CharField(source='advertiser.company_name', read_only=True)
    is_internal_image = serializers.SerializerMethodField()

    class Meta:
        model = Ad
        fields = '__all__'

    def get_is_internal_image(self, obj):
        if not obj.image_url:
            return False
        request = self.context.get('request')
        return bool(storage_key_from_url(obj.image_url, request))

    def validate(self, data):
        start_date = data.get('start_date') or (self.instance.start_date if self.instance else None)
        end_date   = data.get('end_date')   or (self.instance.end_date   if self.instance else None)

        if start_date and end_date and start_date >= end_date:
            raise ValidationError({'end_date': '結束時間必須大於開始時間。'})
        return data

    def _resolve_image_url(self, image_url: str) -> str:
        if image_url:
            request = self.context.get('request')
            if request and request.user.is_authenticated:
                promoted = promote_tmp_ad_photos([image_url], request.user.id, request)
                if promoted:
                    return promoted[0]
        return image_url

    def create(self, validated_data):
        image_url = validated_data.get('image_url')
        if image_url:
            validated_data['image_url'] = self._resolve_image_url(image_url)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        image_url = validated_data.get('image_url')
        if image_url and image_url != instance.image_url:
            validated_data['image_url'] = self._resolve_image_url(image_url)
        return super().update(instance, validated_data)


class AdminSchoolSerializer(serializers.ModelSerializer):
    user_count = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    def get_user_count(self, obj):
        # Prefer the annotation from the list view; fall back to a query so the
        # detail view and bulk import keep working without it.
        annotated = getattr(obj, 'user_count_annotated', None)
        return annotated if annotated is not None else obj.verifications.count()

    def get_display_name(self, obj):
        lang = self.context.get('lang')
        return obj.localized_name(lang) if lang else obj.name

    class Meta:
        model = School
        fields = ('id', 'name', 'display_name', 'email_domain', 'translations', 'region', 'user_count')


class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('id', 'slug', 'title', 'description', 'sort_order', 'is_active', 'translations', 'region')


class AdminUserSerializer(serializers.ModelSerializer):
    """Admin view of a user.

    Accounts are global while verification is per region, so a user can hold
    a TW and an HK verification at once. `regions` and `verifications` are the
    honest representation of that; the flat `school`/`edu_email`/`verified_at`
    fields below are a single-region view kept for the existing admin screens.
    """

    school_name = serializers.SerializerMethodField()
    is_verified = serializers.SerializerMethodField()
    regions = serializers.SerializerMethodField()
    verifications = serializers.SerializerMethodField()

    def _verifications(self, obj):
        """Active verifications, ordered by region code.

        Ordering matters: the flat fields below pick the first row, and an
        unordered .first() over a multi-row relation let the database decide
        which one — so a dual-region user's admin row could show their Taiwan
        school on one request and their Hong Kong school on the next.
        """
        return sorted(
            obj.region_verifications.active(),
            key=lambda v: v.region_id,
        )

    def _primary(self, obj):
        rows = self._verifications(obj)
        return rows[0] if rows else None

    def get_regions(self, obj):
        return [v.region_id for v in self._verifications(obj)]

    def get_verifications(self, obj):
        lang = self.context.get('lang')
        return [
            {
                'region': v.region_id,
                'school': v.school_id,
                'school_name': (v.school.localized_name(lang) if lang else v.school.name) if v.school else '',
                'edu_email': v.edu_email,
                'verified_at': v.verified_at,
            }
            for v in self._verifications(obj)
        ]

    def get_school_name(self, obj):
        v = self._primary(obj)
        if not v or not v.school:
            return ''
        lang = self.context.get('lang')
        return v.school.localized_name(lang) if lang else v.school.name

    edu_email = serializers.SerializerMethodField()
    school = serializers.SerializerMethodField()
    verified_at = serializers.SerializerMethodField()

    def get_edu_email(self, obj):
        v = self._primary(obj)
        return v.edu_email if v else None

    def get_school(self, obj):
        v = self._primary(obj)
        return v.school_id if v else None

    def get_verified_at(self, obj):
        v = self._primary(obj)
        return v.verified_at if v else None

    def get_is_verified(self, obj):
        # Keep memory evaluation since _verifications uses prefetched related manager
        return any(v.verified_at is not None for v in self._verifications(obj))

    class Meta:
        model = User
        fields = (
            'id', 'email', 'first_name', 'last_name', 'display_name',
            'school', 'school_name', 'edu_email', 'is_active', 'is_verified',
            'verified_at', 'created_at', 'is_staff', 'is_superuser',
            'regions', 'verifications',
        )
        read_only_fields = fields


class AdminListingSerializer(serializers.ModelSerializer):
    book = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    school = serializers.SerializerMethodField()

    def get_book(self, obj):
        return {'id': obj.book_id, 'title': obj.book.title}

    def get_seller(self, obj):
        return {'id': obj.seller_id, 'email': obj.seller.email}

    def get_school(self, obj):
        if not obj.school_id:
            return None
        return {'id': obj.school_id, 'name': obj.school.name}

    class Meta:
        model = Listing
        # `region` on the row itself: a superuser sees both regions merged in
        # one list, and without it the only way to tell a TW listing from an HK
        # one was to filter — the unfiltered view was ambiguous.
        fields = ('id', 'book', 'seller', 'school', 'region', 'price', 'currency', 'condition', 'status', 'created_at')
        read_only_fields = fields


class AdminOrderSerializer(serializers.ModelSerializer):
    buyer = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    listing = serializers.SerializerMethodField()

    def get_buyer(self, obj):
        return {'id': obj.buyer_id, 'email': obj.buyer.email}

    def get_seller(self, obj):
        return {'id': obj.seller_id, 'email': obj.seller.email}

    def get_listing(self, obj):
        return {'id': str(obj.listing_id), 'book_title': obj.listing.book.title}

    class Meta:
        model = Order
        fields = ('id', 'buyer', 'seller', 'listing', 'region', 'status', 'total_amount', 'currency', 'created_at', 'updated_at')
        read_only_fields = fields


class AdminChatReportSerializer(serializers.ModelSerializer):
    conversation_id = serializers.SerializerMethodField()
    listing_title = serializers.SerializerMethodField()
    reporter_email = serializers.SerializerMethodField()
    reported_party_email = serializers.SerializerMethodField()

    def get_conversation_id(self, obj):
        return str(obj.conversation_id)

    def get_listing_title(self, obj):
        return obj.conversation.listing.book.title

    def get_reporter_email(self, obj):
        return obj.reporter.email

    def get_reported_party_email(self, obj):
        return obj.reported_party.email

    region = serializers.SerializerMethodField()

    def get_region(self, obj):
        # A chat report has no region of its own; it inherits the region of
        # the listing the conversation is about — the same path adminapi
        # filters on (conversation__listing__region). Derived here rather than
        # in the frontend so the admin table cannot disagree with the filter.
        return obj.conversation.listing.region_id

    class Meta:
        model = ChatReport
        fields = ('id', 'conversation_id', 'listing_title', 'reporter_email',
                  'reported_party_email', 'reason', 'detail', 'status', 'created_at', 'region')
        read_only_fields = fields





class AdminCurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ['code', 'symbol', 'decimal_places', 'symbol_position', 'is_active']
        
    def update(self, instance, validated_data):
        # Reject rather than drop. Both fields were silently popped here, so a
        # PATCH changing decimal_places came back 200 with the old value still
        # in place — the caller had every reason to believe it had worked.
        # That is the wrong failure mode for this field in particular: amounts
        # are stored in minor units, so changing it reinterprets every existing
        # price and order total at once. Say no out loud.
        #
        # Sending the current value is still fine, so a client that PATCHes the
        # whole object back unchanged does not trip over this.
        new_dp = validated_data.pop('decimal_places', None)
        if new_dp is not None and new_dp != instance.decimal_places:
            raise ValidationError({
                "decimal_places": "admin.errCurrencyDecimalPlacesLocked",
            })
        new_code = validated_data.pop('code', None)
        if new_code is not None and new_code != instance.code:
            raise ValidationError({"code": "admin.errCurrencyCodeLocked"})
        return super().update(instance, validated_data)

class AdminRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = [
            'code', 'name', 'translations', 'currency', 'default_language', 
            'languages', 'timezone', 'search_engines', 'edu_email_suffix', 
            'is_active', 'sort_order'
        ]
        
    def validate(self, attrs):
        default_lang = attrs.get('default_language')
        languages = attrs.get('languages')
        
        if self.instance:
            default_lang = default_lang or self.instance.default_language
            if 'languages' in attrs:
                languages = attrs['languages']
            else:
                languages = self.instance.languages.all()
                
        if languages is not None and default_lang is not None:
            if default_lang not in languages:
                raise ValidationError({"default_language": "default_language must be one of the selected languages."})
                
        translations = attrs.get('translations')
        if self.instance:
            if 'translations' in attrs and not translations:
                raise ValidationError({"translations": "translations cannot be empty."})
        else:
            if not translations:
                raise ValidationError({"translations": "translations cannot be empty."})
            if not languages:
                raise ValidationError({"languages": "languages cannot be empty."})
                
        return attrs
        
    def update(self, instance, validated_data):
        validated_data.pop('code', None)
        
        is_active = validated_data.get('is_active')
        if is_active is False and instance.is_active is True:
            if Region.objects.filter(is_active=True).exclude(code=instance.code).count() == 0:
                raise ValidationError({"is_active": "Cannot disable the last active region."})
                
        return super().update(instance, validated_data)

"""Read-only serializers backing the Angular admin UI (users / listings / orders).

These are deliberately manual/read-only: writes for each resource are handled
by explicit, narrowly-scoped logic in views.py rather than generic
ModelSerializer.update(), so we can enforce field allow-lists (e.g. never
letting is_staff/is_superuser/password through) precisely per-endpoint.
"""
from rest_framework import serializers

from accounts.models import User, School
from core.models import Category
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

    def get_user_count(self, obj):
        # Prefer the annotation from the list view; fall back to a query so the
        # detail view and bulk import keep working without it.
        annotated = getattr(obj, 'user_count_annotated', None)
        return annotated if annotated is not None else obj.users.count()

    class Meta:
        model = School
        fields = ('id', 'name', 'email_domain', 'translations', 'user_count')


class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('id', 'slug', 'title', 'description', 'sort_order', 'is_active', 'translations')


class AdminUserSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()
    is_verified = serializers.SerializerMethodField()

    def get_school_name(self, obj):
        if not obj.school:
            return ''
        lang = self.context.get('lang')
        return obj.school.localized_name(lang) if lang else obj.school.name

    def get_is_verified(self, obj):
        return obj.is_verified()

    class Meta:
        model = User
        fields = (
            'id', 'email', 'first_name', 'last_name', 'display_name',
            'school', 'school_name', 'edu_email', 'is_active', 'is_verified',
            'verified_at', 'created_at', 'is_staff', 'is_superuser',
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
        fields = ('id', 'book', 'seller', 'school', 'price', 'condition', 'status', 'created_at')
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
        fields = ('id', 'buyer', 'seller', 'listing', 'status', 'total_amount', 'created_at', 'updated_at')
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

    class Meta:
        model = ChatReport
        fields = ('id', 'conversation_id', 'listing_title', 'reporter_email',
                  'reported_party_email', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = fields




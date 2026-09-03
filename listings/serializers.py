from urllib.parse import urlparse

from rest_framework import serializers
from listings.models import Listing

from core.models import Category
# Shared with messaging/ — `photos` is populated straight from client input,
# not derived from the upload endpoints, so without a host allowlist it could
# point anywhere (a tracking pixel or phishing image on an attacker domain).
from core.region import get_region
from core.uploads import allowed_storage_hosts
from listings.utils import promote_tmp_photos

MAX_LISTING_PHOTOS = 6


class ListingSerializer(serializers.ModelSerializer):
    seller_name = serializers.SerializerMethodField()
    seller_avatar_url = serializers.SerializerMethodField()
    school_name = serializers.SerializerMethodField()
    seller_school_id = serializers.IntegerField(source='school_id', read_only=True, default=None)
    book_title = serializers.CharField(source='book.title', read_only=True, default='')
    book_authors = serializers.CharField(source='book.authors', read_only=True, default='')
    book_cover_url = serializers.CharField(source='book.cover_url', read_only=True, default='')
    book_source = serializers.CharField(source='book.source', read_only=True, default='')
    course_name = serializers.CharField(required=False, allow_blank=True, default='')
    professor_name = serializers.CharField(required=False, allow_blank=True, default='')
    isbn = serializers.CharField(source='book.isbn13', read_only=True, default='')
    category = serializers.SlugRelatedField(
        slug_field='slug',
        queryset=Category.objects.filter(is_active=True),
        required=False,
        allow_null=True
    )
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = '__all__'
        read_only_fields = ('seller', 'school', 'region', 'currency', 'created_at', 'updated_at')

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # `private_note` is seller-only. It is dropped for anyone but the
        # seller, and unconditionally when the caller marks the payload as
        # bound for a shared cache (`strip_private_note`): the public listing
        # and book endpoints cache the serialized body under a key that
        # carries no user identity, so the seller's own first view would
        # otherwise seed the cache with the note and serve it to everyone
        # who loads the page after them.
        request = self.context.get('request')
        is_seller = bool(
            request is not None
            and request.user.is_authenticated
            and request.user.id == instance.seller_id
        )
        if self.context.get('strip_private_note') or not is_seller:
            rep.pop('private_note', None)
        return rep

    def get_seller_name(self, obj):
        if not obj.seller:
            return ''
        return obj.seller.display_name

    def get_seller_avatar_url(self, obj):
        if not obj.seller:
            return ''
        return obj.seller.avatar_url

    def get_photo_url(self, obj):
        if obj.photos and len(obj.photos) > 0:
            return obj.photos[0]
        return ''

    def validate_book(self, value):
        """A listing may only point at a book from its own region.

        `region` is read-only on this serializer, so a seller cannot move a
        listing between regions — but `book` is writable, and on PATCH that
        was enough: repointing a TW listing at an HK book left region=TW with
        book.region=HK, so HK catalogue content surfaced inside TW's listing
        feed. Listing.clean() already forbids the combination, but DRF never
        calls full_clean(), so nothing enforced it on the write path.

        On create there is no instance yet; the region comes from the request
        and the view sets it at save() time, so validate against that instead.
        """
        region = self.instance.region if self.instance else get_region(self.context['request'])
        if region and value.region_id != region.pk:
            raise serializers.ValidationError('listing.errBookRegionMismatch')
        return value

    def validate_photos(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('listing.errInvalidPhotos')
        if len(value) > MAX_LISTING_PHOTOS:
            raise serializers.ValidationError('listing.errTooManyPhotos')

        allowed_hosts = allowed_storage_hosts(self.context.get('request'))
        for url in value:
            if not isinstance(url, str):
                raise serializers.ValidationError('listing.errInvalidPhotos')
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https') or parsed.hostname is None:
                raise serializers.ValidationError('listing.errInvalidPhotos')
            if parsed.hostname.lower() not in allowed_hosts:
                raise serializers.ValidationError('listing.errPhotoHostNotAllowed')
        return value

    def get_school_name(self, obj):
        school = obj.school
        if not school:
            return ''
        request = self.context.get('request')
        if request:
            from core.i18n import resolve_language
            lang = resolve_language(request)
            return school.localized_name(lang)
        return school.name

    def _promote_photos(self, validated_data):
        """Move freshly-uploaded `tmp/` photos to their permanent keys.

        Scoped to the requesting user so a listing can't adopt someone else's
        pending upload — see listings.utils.promote_tmp_photos.
        """
        if 'photos' not in validated_data:
            return
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            raise serializers.ValidationError('listing.errInvalidPhotos')
        validated_data['photos'] = promote_tmp_photos(
            validated_data['photos'], user.id, request=request
        )

    def create(self, validated_data):
        self._promote_photos(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        self._promote_photos(validated_data)
        return super().update(instance, validated_data)

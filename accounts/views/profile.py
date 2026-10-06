import logging

from django.db.models import Count, Q, prefetch_related_objects
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework import status, views
from core.throttling import ScopedThrottle
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from core.cache import bump_cache_version
from core.i18n import EMAIL_LANGUAGES
from core.region import get_region
from django.contrib.auth import get_user_model

from accounts.models import School
from accounts.serializers import NotificationSettingsSerializer, PublicUserProfileSerializer, UserSerializer
from listings.models import Listing
from listings.serializers import ListingSerializer, with_seller_stats
from subscriptions.models import subscriptions_with_new_listings_count
from subscriptions.serializers import SubscriptionSerializer
from catalog.book_search import book_search_q

logger = logging.getLogger(__name__)

User = get_user_model()


# How the seller's own listings page may order them. Every ordering ends on
# `-created_at` so equal prices keep a stable, meaningful order.
LISTING_SORTS = {
    'newest': ('-created_at',),
    'oldest': ('created_at',),
    'price_asc': ('price', '-created_at'),
    'price_desc': ('-price', '-created_at'),
}


def invalidate_seller_caches(user):
    """Retire cached pages that embed `user` as a seller.

    Listing feeds, a book's page and each listing's page carry the seller's
    avatar, and are cached for minutes; without this, hiding the avatar would
    leave it on show there until they expired.
    """
    bump_cache_version('listing_list')
    bump_cache_version('book_detail')
    for pk in user.listings.values_list('pk', flat=True):
        bump_cache_version(f'listing:{pk}')


class MyProfileView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # UserSerializer walks region_verifications (and each one's school)
        # from half a dozen method fields; without this every one of them
        # re-queries the relation.
        prefetch_related_objects([user], 'region_verifications__school')
        serializer = UserSerializer(user, context={'request': request})
        data = serializer.data

        # Related data the frontend My Account page needs
        region = get_region(request)
        my_listings = with_seller_stats(user.listings.filter(region=region).select_related('book', 'seller', 'school'))

        # The account page's "N active · N sold" counted the page of listings
        # below, so a seller with more than a page of them was undercounted.
        # Counted before the search and status filters: the totals describe
        # the account, and the listings page labels its status tabs with them.
        counts = dict(
            user.listings.filter(region=region)
            .order_by().values('status').annotate(n=Count('id')).values_list('status', 'n')
        )
        data['myListingCounts'] = {
            **{value: counts.get(value, 0) for value, _ in Listing.STATUS_CHOICES},
            'all': sum(counts.values()),
        }

        status_filter = request.query_params.get('status', '').strip()
        if status_filter in dict(Listing.STATUS_CHOICES):
            my_listings = my_listings.filter(status=status_filter)

        q = request.query_params.get('q', '').strip()
        if q:
            my_listings = my_listings.filter(
                book_search_q(q, prefix='book__')
                | Q(course_name__icontains=q)
                | Q(professor_name__icontains=q)
            )

        my_listings = my_listings.order_by(*LISTING_SORTS.get(request.query_params.get('sort'), LISTING_SORTS['newest']))

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(my_listings, request)

        if page is not None:
            data['myListings'] = {
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': ListingSerializer(page, many=True, context={'request': request}).data
            }
        else:
            data['myListings'] = {
                'count': my_listings.count(),
                'next': None,
                'previous': None,
                'results': ListingSerializer(my_listings, many=True, context={'request': request}).data
            }

        from accounts.views.auth import pending_email_change
        data['pending_email'] = pending_email_change(user)

        my_subs = subscriptions_with_new_listings_count(user.subscriptions.filter(region=region))
        data['mySubscriptions'] = SubscriptionSerializer(my_subs, many=True).data

        return Response(data)

    def patch(self, request):
        user = request.user
        first_name = request.data.get('first_name')
        last_name = request.data.get('last_name')
        email = request.data.get('email')
        last_seen_bought_orders_at = request.data.get('last_seen_bought_orders_at')
        last_seen_sold_orders_at = request.data.get('last_seen_sold_orders_at')
        show_avatar = request.data.get('show_avatar')

        if show_avatar is not None and not isinstance(show_avatar, bool):
            return Response({"show_avatar": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)
        if first_name is not None and not isinstance(first_name, str):
            return Response({"first_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)
        if last_name is not None and not isinstance(last_name, str):
            return Response({"last_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)

        updated_fields = []
        avatar_visibility_changed = show_avatar is not None and show_avatar != user.show_avatar
        if avatar_visibility_changed:
            user.show_avatar = show_avatar
            updated_fields.append('show_avatar')
        if first_name is not None:
            user.first_name = first_name.strip()
            updated_fields.append('first_name')
        if last_name is not None:
            user.last_name = last_name.strip()
            updated_fields.append('last_name')
        if email is not None:
            email = email.strip().lower()
            if email != user.email:
                if user.socialaccount_set.filter(provider='google').exists():
                    return Response({"email": [_("You cannot change the email of a Google-linked account.")]}, status=status.HTTP_400_BAD_REQUEST)
                if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    return Response({"email": [_("Email is already in use.")]}, status=status.HTTP_400_BAD_REQUEST)
                # A login email matching a supported school's domain is what
                # AutoVerifyEduEmailView trusts to grant verified-student
                # status with no further proof — so this endpoint must never
                # let that value become one the user hasn't actually proven
                # ownership of. Legitimate school-email logins are still
                # possible (set at registration, proven via the activation
                # link), just not by editing it in afterward.
                #
                # Uses the same suffix-aware matcher the auto-verify view
                # trusts. An exact-domain check here was bypassable: School
                # rows match any subdomain (`mail.ntu.edu.tw` resolves to
                # `ntu.edu.tw`), so `me@mail.ntu.edu.tw` sailed through this
                # check and was then accepted as a campus address.
                from accounts.views.auth import _is_valid_edu_email, send_email_change_verification
                if _is_valid_edu_email(email):
                    return Response({"error": {"code": "acct.errEduEmailChangeNotAllowed"}}, status=status.HTTP_400_BAD_REQUEST)
                # Not applied here. The address is only proven by reading mail
                # sent to it, and until this went through a confirmation link
                # anyone with a session could point the account at a mailbox
                # they did not own — after which password-reset mail went
                # there too. Any other fields in this request still save —
                # but save them *first*: send_email_change_verification writes
                # a token row and sends mail with no rollback of its own,
                # so if the other fields failed to save that would leave a
                # working confirmation link for a request that otherwise
                # errored.
                if updated_fields:
                    user.save(update_fields=updated_fields)
                send_email_change_verification(request, user, email)
                return Response({"code": "acct.emailChangePending", "pending_email": email})

        if last_seen_bought_orders_at is not None:
            parsed = parse_datetime(last_seen_bought_orders_at) if last_seen_bought_orders_at else None
            user.last_seen_bought_orders_at = parsed
            updated_fields.append('last_seen_bought_orders_at')

        if last_seen_sold_orders_at is not None:
            parsed = parse_datetime(last_seen_sold_orders_at) if last_seen_sold_orders_at else None
            user.last_seen_sold_orders_at = parsed
            updated_fields.append('last_seen_sold_orders_at')

        if updated_fields:
            user.save(update_fields=updated_fields)
        if avatar_visibility_changed:
            invalidate_seller_caches(user)

        serializer = UserSerializer(user, context={'request': request})
        return Response(serializer.data)

    def delete(self, request):
        # The anonymize-rather-than-remove logic lives on User.delete()
        # (accounts/models.py) so every path that deletes a User — this view,
        # `manage.py shell`, a single object deleted from Django admin — goes
        # through it, not just whichever call site happened to remember to
        # ask for it.
        request.user.delete()
        return Response({"code": "acct.deleted"}, status=status.HTTP_204_NO_CONTENT)


class NotificationSettingsView(views.APIView):
    """GET/PATCH the signed-in user's notification switches.

    Its own endpoint rather than more fields on /auth/me/: that one answers
    with listings and subscriptions on GET and runs the email-change flow on
    PATCH, neither of which a switch on the Notifications page needs.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(NotificationSettingsSerializer(request.user).data)

    def patch(self, request):
        serializer = NotificationSettingsSerializer(request.user, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


class SiteLanguageView(views.APIView):
    """PUT the language the signed-in user is using the site in.

    The frontend reports it whenever it differs from what /auth/me/ says, so
    this is "the language they last used", which notification emails follow
    unless the user picked one (NotificationSettingsView).
    """
    permission_classes = [IsAuthenticated]

    def put(self, request):
        language = request.data.get('language')
        if language not in EMAIL_LANGUAGES:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
        if request.user.site_language != language:
            request.user.site_language = language
            request.user.save(update_fields=['site_language'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SiteRegionView(views.APIView):
    """PUT the region the signed-in user is using the site in.

    The counterpart of SiteLanguageView, brought back on the next sign-in.
    Kept only for a region the user has verified a school email in: that is
    where they trade, while the region they happen to be browsing may be
    nothing of theirs.
    """
    permission_classes = [IsAuthenticated]

    def put(self, request):
        code = request.data.get('region')
        if not isinstance(code, str) or not request.user.is_verified_in(code.upper()):
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
        code = code.upper()
        if request.user.site_region != code:
            request.user.site_region = code
            request.user.save(update_fields=['site_region'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicUserProfileView(views.APIView):
    # Sequential integer ids, a name and a join date per hit: unthrottled this
    # is a directory of every account on the site, walkable in one pass.
    permission_classes = [AllowAny]
    throttle_classes = [ScopedThrottle]
    throttle_scope = 'public_profile'

    def get(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PublicUserProfileSerializer(user, context={'request': request})
        return Response(serializer.data)

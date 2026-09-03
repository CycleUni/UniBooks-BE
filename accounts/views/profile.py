import logging
import uuid

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, prefetch_related_objects
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from rest_framework import status, views
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from core.region import get_region
from core.i18n import resolve_language
from django.contrib.auth import get_user_model

from accounts.models import School
from accounts.serializers import PublicUserProfileSerializer, UserSerializer
from listings.serializers import ListingSerializer
from subscriptions.models import subscriptions_with_new_listings_count
from subscriptions.serializers import SubscriptionSerializer

logger = logging.getLogger(__name__)

User = get_user_model()


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
        my_listings = user.listings.filter(region=region).select_related('book', 'seller', 'school').order_by('-created_at')

        q = request.query_params.get('q', '').strip()
        if q:
            my_listings = my_listings.filter(
                Q(book__title__icontains=q) |
                Q(book__authors__icontains=q) |
                Q(book__isbn13__icontains=q)
            )

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

        if first_name is not None and not isinstance(first_name, str):
            return Response({"first_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)
        if last_name is not None and not isinstance(last_name, str):
            return Response({"last_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)

        updated_fields = []
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
                # there too. Any other fields in this request still save.
                send_email_change_verification(request, user, email)
                if updated_fields:
                    user.save(update_fields=updated_fields)
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

        serializer = UserSerializer(user, context={'request': request})
        return Response(serializer.data)

    def delete(self, request):
        """Delete the account by emptying it, not by removing the row.

        Order.buyer/seller and Review.reviewer/reviewee are CASCADE from both
        sides, so `user.delete()` took the counterparty's purchase history and
        the ratings they had earned down with it — one person leaving erased
        another person's record. Everything personal goes; what is left is an
        anonymous row the surviving orders and reviews can still point at.
        """
        from accounts.services import revoke_all_tokens_for_user
        from accounts.views.auth import email_change_pending_key

        user = request.user
        lang = resolve_language(request)
        marker = {
            'zh-TW': '已刪除的帳號',
            'zh-HK': '已刪除的帳戶',
        }.get(lang, 'Deleted account')

        with transaction.atomic():
            # One save each rather than a bulk update: post_save is what bumps
            # the listing cache generation, and a queryset update never fires
            # it, so the listings would stay in every cached feed.
            for listing in user.listings.exclude(status='removed'):
                listing.status = 'removed'
                listing.save(update_fields=['status'])

            # Campus addresses and waitlist rows are personal data with
            # nothing pointing at them; they go for real.
            user.region_verifications.all().delete()
            user.subscriptions.all().delete()
            user.socialaccount_set.all().delete()

            user.email = f"deleted-{uuid.uuid4().hex}@deleted.invalid"
            user.first_name = marker
            user.last_name = ''
            user.avatar_url = ''
            user.is_active = False
            user.deleted_at = timezone.now()
            user.set_unusable_password()
            user.save(update_fields=[
                'email', 'first_name', 'last_name', 'avatar_url',
                'is_active', 'deleted_at', 'password',
            ])

        cache.delete(email_change_pending_key(user.id))
        revoke_all_tokens_for_user(str(user.id))
        return Response({"code": "acct.deleted"}, status=status.HTTP_204_NO_CONTENT)


class PublicUserProfileView(views.APIView):
    # Sequential integer ids, a name and a join date per hit: unthrottled this
    # is a directory of every account on the site, walkable in one pass.
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_profile'

    def get(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PublicUserProfileSerializer(user, context={'request': request})
        return Response(serializer.data)

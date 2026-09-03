import hmac
import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, F, Max, Q
from django.utils import timezone
from rest_framework import views, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from subscriptions.models import Subscription

logger = logging.getLogger(__name__)

# Recipients per run; see WaitlistNotifyView for why there is a cap at all.
MAX_NOTIFY_USERS_PER_RUN = 50


class HasCronSecret(BasePermission):
    """Authenticates cron-triggered requests via `Authorization: Bearer <CRON_SECRET>`
    instead of a user session — there's no logged-in user calling this endpoint,
    just a scheduler (e.g. Vercel Cron hitting a path in this same deployment).
    Unset CRON_SECRET fails closed (permission denied), matching this project's
    other optional-feature secrets (EDGE_CHAT_WEBHOOK_SECRET etc.)."""

    def has_permission(self, request, view):
        if not settings.CRON_SECRET:
            return False
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return False
        provided = auth_header[len('Bearer '):]
        return hmac.compare_digest(provided, settings.CRON_SECRET)


class WaitlistNotifyView(views.APIView):
    """Emails everyone on a book's waitlist (Subscription) once a new active
    listing appears for it, then marks them notified so re-running this
    (the scheduler may retry, or fire more than once) doesn't double-send.

    GET because Vercel Cron only ever issues GET requests to the configured
    path; POST also accepted for manual/local triggering.
    """
    # The `Authorization: Bearer <CRON_SECRET>` header here is not a JWT —
    # it must not go through the project-wide JWTAuthentication (which would
    # reject it as an invalid token with 401 before HasCronSecret ever runs).
    authentication_classes = []
    permission_classes = [HasCronSecret]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'cron'

    def get(self, request):
        return self._run()

    def post(self, request):
        return self._run()

    def _run(self):
        now = timezone.now()

        # A subscription is "due" when the book has an active listing created
        # after the subscription started, and after the last time this
        # subscription was notified (or never, if notified_at is unset).
        due = (
            Subscription.objects
            .select_related('user', 'book')
            .annotate(
                latest_active_listing_at=Max(
                    'book__listings__created_at',
                    filter=Q(book__listings__status='active'),
                )
            )
            .filter(latest_active_listing_at__gt=F('created_at'))
            .filter(Q(notified_at__isnull=True) | Q(latest_active_listing_at__gt=F('notified_at')))
        )

        # One email per user, even if several of their subscriptions are due —
        # nobody wants a separate email per book.
        by_user = defaultdict(list)
        for sub in due.order_by('created_at'):
            by_user[sub.user].append(sub)

        notified_users = 0
        notified_subscriptions = 0

        # Sending is sequential and synchronous, and this runs inside a Vercel
        # function capped at maxDuration seconds: past a few dozen recipients
        # the run is killed part-way through, and because notified_at is only
        # written after a successful send, the next run simply resumes with
        # whoever is still due. Capping it makes that the normal path instead
        # of the failure path. `remaining_users` says whether the scheduler
        # has more to collect.
        batch = list(by_user.items())[:MAX_NOTIFY_USERS_PER_RUN]
        remaining_users = len(by_user) - len(batch)

        for user, subs in batch:
            book_lines = []
            for sub in subs:
                book_url = f"{settings.FRONTEND_URL}/book?isbn={sub.book.isbn13}" if sub.book.isbn13 else settings.FRONTEND_URL
                book_lines.append(f"- {sub.book.title}: {book_url}")
            books_block = "\n".join(book_lines)

            subject = "UniBooks 到貨通知 / New listings for your waitlist"
            message = (
                f"您求書清單中的以下書籍已有新上架商品：\n\n{books_block}\n\n"
                "登入 UniBooks 查看詳情。\n\n---\n\n"
                f"New listings are available for books on your UniBooks waitlist:\n\n{books_block}\n\n"
                "Log in to UniBooks to view them."
            )

            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Failed to send waitlist notification to user %s", user.id)
                continue

            Subscription.objects.filter(id__in=[s.id for s in subs]).update(notified_at=now)
            notified_users += 1
            notified_subscriptions += len(subs)

        return Response({
            "notified_users": notified_users,
            "notified_subscriptions": notified_subscriptions,
            "remaining_users": remaining_users,
        }, status=status.HTTP_200_OK)


class CleanupView(views.APIView):
    """Deletes books that have no listings and no subscriptions.

    Called periodically by an external scheduler (Vercel Cron via GET).
    Uses the same HasCronSecret authentication as WaitlistNotifyView.
    """
    authentication_classes = []
    permission_classes = [HasCronSecret]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'cron'

    def get(self, request):
        return self._run()

    def post(self, request):
        return self._run()

    def _run(self):
        from catalog.models import Book

        # Books with zero listings (any status) AND zero subscriptions
        orphans = (
            Book.objects
            .annotate(
                listing_count=Count('listings'),
                subscription_count=Count('subscriptions'),
            )
            .filter(listing_count=0, subscription_count=0)
        )

        total_scanned = Book.objects.count()
        to_delete = orphans.count()
        orphans.delete()

        logger.info("Cleanup: deleted %d orphan books out of %d total", to_delete, total_scanned)

        return Response({
            "orphan_books_deleted": to_delete,
            "scanned_books": total_scanned,
        }, status=status.HTTP_200_OK)
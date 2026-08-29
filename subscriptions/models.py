import uuid
from django.db import models
from django.db.models import Count, F, Q
from django.conf import settings
from catalog.models import Book
from accounts.models import School
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from core.cache import safe_cache_delete


def subscriptions_with_new_listings_count(queryset):
    """Annotate `new_listings_count` (active listings created after subscribing)
    so SubscriptionSerializer can render lists without per-object queries."""
    return queryset.select_related('book').annotate(
        new_listings_count=Count(
            'book__listings',
            filter=Q(
                book__listings__status='active',
                book__listings__created_at__gt=F('created_at'),
            ),
            distinct=True,
        )
    )

class Subscription(models.Model):
    region = models.ForeignKey('core.Region', on_delete=models.CASCADE)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscriptions')
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='subscriptions')
    school = models.ForeignKey(School, on_delete=models.SET_NULL, null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'book')
        indexes = [
            models.Index(fields=['user', 'region']),
        ]
        indexes = [
            models.Index(fields=['user', 'region']),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.book.title}"

@receiver([post_save, post_delete], sender=Subscription)
def invalidate_home_waitlist_cache(sender, **kwargs):
    """Bust the homepage "most wanted" list when a subscription changes."""
    safe_cache_delete("home_waitlist")

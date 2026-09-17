import uuid
from django.db import models
from django.conf import settings
from listings.models import Listing

class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='conversations')
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations_as_buyer')

    latest_message_body = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # When the buyer first opened the chat. Null for conversations started
    # before this was recorded; statistics leave those out of timed figures.
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    # --- Read-state (DEPRECATED — now owned by CFEdgeChat UserHub) ---
    # DEPRECATED: Read-state is now managed by CFEdgeChat's UserHub
    # (POST /api/<app>/<room>/read). These fields are no longer updated
    # by the webhook — kept only to avoid a new migration.
    buyer_last_read_at = models.DateTimeField(null=True, blank=True)
    seller_last_read_at = models.DateTimeField(null=True, blank=True)

    # Soft-delete per participant: a conversation is hidden from a user
    # once they delete it. When both parties have deleted it the whole row
    # is permanently removed (see `mark_deleted_by`).
    buyer_deleted_at = models.DateTimeField(null=True, blank=True)
    seller_deleted_at = models.DateTimeField(null=True, blank=True)

    def mark_deleted_by(self, user):
        """Set the caller's deletion timestamp; remove the row if both are set."""
        from django.utils import timezone
        if self.buyer_id == user.id:
            field = 'buyer_deleted_at'
        elif self.listing.seller_id == user.id:
            field = 'seller_deleted_at'
        else:
            return False  # not a participant
        setattr(self, field, timezone.now())
        # Only this field: a plain save() also moved `updated_at` (auto_now),
        # which the inbox sorts by and shows as the time of the latest message
        # — so one side deleting the conversation made it jump to the top of
        # the *other* side's inbox, stamped with a time nobody wrote anything.
        self.save(update_fields=[field])
        if self.buyer_deleted_at and self.seller_deleted_at:
            self.delete()
            return 'deleted'
        return 'hidden'

    class Meta:
        unique_together = ('listing', 'buyer')

    def __str__(self):
        return f"Conversation on {self.listing.id} by {self.buyer.email}"




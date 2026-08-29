import uuid
from django.db import models
from django.conf import settings

class Order(models.Model):
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    STATUS_CHOICES = [
        ('pending', 'Pending (Requested)'),
        ('accepted', 'Accepted (Awaiting Meetup)'),
        ('handed_over', 'Handed Over (Awaiting Buyer Confirmation)'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders_bought')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders_sold')
    listing = models.ForeignKey('listings.Listing', on_delete=models.CASCADE, related_name='orders')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    cancel_reason = models.CharField(max_length=50, blank=True, null=True)
    
    total_amount = models.PositiveIntegerField(help_text="Order total amount in TWD")
    
    meetup_time = models.DateTimeField(null=True, blank=True, help_text="Agreed meetup time")
    meetup_location = models.CharField(max_length=255, blank=True, help_text="Agreed meetup location")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order #{self.id} - {self.listing.book.title} ({self.status})"

class Review(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews_given')
    reviewee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews_received')
    
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1 to 5 rating. Null if no-show report.")
    comment = models.TextField(blank=True)
    is_no_show = models.BooleanField(default=False, help_text="True if this is a report for a no-show.")
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('order', 'reviewer', 'reviewee')

    def __str__(self):
        return f"Review by {self.reviewer} for {self.reviewee} on Order {self.order.id}"

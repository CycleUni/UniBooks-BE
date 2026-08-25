from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

class Advertiser(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='advertiser_profile',
        help_text="Reserved for future advertiser login"
    )
    company_name = models.CharField(max_length=255, help_text="Company name")
    contact_email = models.EmailField(help_text="Contact email")
    contact_phone = models.CharField(max_length=50, blank=True, help_text="Contact phone")
    
    all_schools = models.BooleanField(default=True, help_text="Whether it applies to all schools")
    schools = models.ManyToManyField(
        'accounts.School', 
        related_name='advertisers', 
        blank=True, 
        help_text="Specific schools it applies to"
    )
    
    is_active = models.BooleanField(default=True, help_text="Whether it is active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.company_name

class Ad(models.Model):
    POSITION_CHOICES = [('home_banner', 'Home Banner')]

    advertiser = models.ForeignKey(Advertiser, on_delete=models.CASCADE, related_name='ads', help_text="Advertiser it belongs to")
    title = models.CharField(max_length=255, help_text="Ad title, for internal identification")
    image_url = models.URLField(max_length=500, help_text="Recommended 5:7 portrait, min 870x1218 (for largest cell 434x607 @2x)")
    target_url = models.URLField(max_length=500, help_text="Target URL after click")
    position = models.CharField(max_length=50, choices=POSITION_CHOICES, default='home_banner', help_text="Ad position")
    
    headline = models.CharField(max_length=255, blank=True, help_text="Headline")
    subheadline = models.CharField(max_length=255, blank=True, help_text="Subheadline")
    slot_index = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(200)],
        help_text="Display order (1-indexed, max 200)"
    )
    from ads.validators import validate_ad_labels
    labels = models.JSONField(
        default=list, 
        blank=True, 
        validators=[validate_ad_labels],
        help_text="Custom labels (max 3, max 15 chars each)"
    )
    start_date = models.DateTimeField(help_text="Start date")
    end_date = models.DateTimeField(help_text="End date")
    
    is_active = models.BooleanField(default=True, help_text="Whether it is active")
    show_in_hero = models.BooleanField(default=False, help_text="Whether to display this ad as the first card in the home page hero cover stack")
    # NOTE: This field **MUST** be updated via F() expression only to avoid concurrent race conditions
    clicks_count = models.IntegerField(default=0, help_text="Click count statistics (update via F() expression only)")
    views_count = models.IntegerField(default=0, help_text="View count statistics (update via F() expression only)")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_active', 'position', 'start_date', 'end_date'], name='ad_active_pos_period_idx'),
        ]

    def __str__(self):
        return f"{self.title} ({self.advertiser.company_name})"

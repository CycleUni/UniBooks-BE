from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.contrib.postgres.indexes import GinIndex
from django.utils import timezone


class School(models.Model):
    """School registry, used to derive a user's school from their email domain.

    `name` is the canonical (English) name. Localized names live in
    `translations`, keyed by language code:
    {"zh-TW": {"name": "國立台灣大學"}, "ja": {"name": "..."}}
    """
    email_domain = models.CharField(max_length=255, unique=True, help_text="e.g.: ntu.edu.tw")
    name = models.CharField(max_length=255, help_text="Canonical English name, e.g. National Taiwan University")
    translations = models.JSONField(default=dict, blank=True, help_text='Localized fields per language, e.g. {"zh-TW": {"name": "國立台灣大學"}}')
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT, related_name='schools')

    def localized_name(self, lang):
        """Name in the requested language, falling back to the canonical name."""
        from core.i18n import pick_translation
        return pick_translation(self.translations, lang).get('name') or self.name

    def __str__(self):
        return self.name

    class Meta:
        indexes = [
            GinIndex(
                name='school_trgm_idx',
                fields=['name', 'email_domain'],
                opclasses=['gin_trgm_ops', 'gin_trgm_ops']
            )
        ]



class UserManager(BaseUserManager):
    def create_user(self, email, first_name, last_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Users must have an email address')
        
        email = self.normalize_email(email)
        user = self.model(
            email=email,
            first_name=first_name,
            last_name=last_name,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, first_name, last_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, first_name, last_name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model keyed by email, linked to the user's school."""
    email = models.EmailField(unique=True, help_text="Registration email, can be any email")
    first_name = models.CharField(max_length=150, default='')
    last_name = models.CharField(max_length=150, default='')
    avatar_url = models.URLField(max_length=500, null=True, blank=True, help_text="Avatar URL")
    
    
    last_seen_bought_orders_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the most recent bought order seen by the user")
    last_seen_sold_orders_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the most recent sold order seen by the user")
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    managed_regions = models.ManyToManyField('core.Region', blank=True, related_name='managers')

    class Meta:
        indexes = [
            GinIndex(
                name='user_trgm_idx',
                fields=['email', 'first_name', 'last_name'],
                opclasses=['gin_trgm_ops', 'gin_trgm_ops', 'gin_trgm_ops']
            )
        ]

    def __str__(self):
        return self.email

    def is_verified_in(self, region):
        """Check if user is verified in the specified region (by Region instance or string code)."""
        return self.region_verifications.verified_in(region).exists()

    @property
    def verified_regions(self):
        from core.models import Region
        return Region.objects.filter(verifications__user=self, verifications__is_active=True, verifications__verified_at__isnull=False)

    @property
    def display_name(self):
        last = self.last_name or ''
        first = self.first_name or ''
        if last and first:
            import re
            if re.search(r'[A-Za-z]', last) or re.search(r'[A-Za-z]', first):
                return f"{first} {last}"
            return f"{last}{first}"
        return f"{last}{first}".strip()

    @property
    def average_rating(self):
        from django.db.models import Avg
        # One aggregate; the previous exists()+aggregate pair cost two queries
        # per serialized user for the same answer.
        avg = self.reviews_received.filter(is_no_show=False, rating__isnull=False).aggregate(avg=Avg('rating'))['avg']
        return round(avg, 1) if avg is not None else 0.0

    @property
    def review_count(self):
        return self.reviews_received.filter(is_no_show=False, rating__isnull=False).count()

    @property
    def no_show_count(self):
        return self.reviews_received.filter(is_no_show=True).count()



class RegionVerificationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def active_in(self, region):
        code = region.code if hasattr(region, 'code') else region
        return self.active().filter(region_id=code)

    def verified(self):
        return self.active().filter(verified_at__isnull=False)

    def verified_in(self, region):
        code = region.code if hasattr(region, 'code') else region
        return self.verified().filter(region_id=code)

class RegionVerification(models.Model):
    objects = RegionVerificationQuerySet.as_manager()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='region_verifications')
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT, related_name='verifications') # Will make null=False
    school = models.ForeignKey(School, on_delete=models.SET_NULL, null=True, blank=True, related_name='verifications')
    edu_email = models.EmailField(unique=True, null=True, blank=True, help_text="Null for manual verifications.")
    is_manual_verification = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_reverified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'region'], name='one_verification_per_user_region'),
        ]

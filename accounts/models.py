import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.contrib.postgres.indexes import GinIndex
from django.core.validators import RegexValidator
from django.utils import timezone

from accounts.school_codes import (
    CODE_INPUT_PATTERN, CODE_MAX_LENGTH, dedupe_code, derive_code, normalize_code,
)


class School(models.Model):
    """School registry, used to derive a user's school from their email domain.

    `name` is the canonical (English) name. Localized names live in
    `translations`, keyed by language code:
    {"zh-TW": {"name": "國立台灣大學"}, "ja": {"name": "..."}}

    `code` is the short identifier the frontend selects and links by ("NTU",
    "HKU"). It is unique per region, not globally: Taiwan's Hung Kuang and
    the University of Hong Kong are both HKU. See accounts.school_codes.
    """
    email_domain = models.CharField(max_length=255, unique=True, help_text="e.g.: ntu.edu.tw")
    name = models.CharField(max_length=255, help_text="Canonical English name, e.g. National Taiwan University")
    translations = models.JSONField(default=dict, blank=True, help_text='Localized fields per language, e.g. {"zh-TW": {"name": "國立台灣大學"}}')
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT, related_name='schools')
    code = models.CharField(
        max_length=CODE_MAX_LENGTH,
        blank=True,
        # Lowercase accepted: this runs on form input before save() uppercases it.
        validators=[RegexValidator(CODE_INPUT_PATTERN, 'Use only letters, digits and "-".')],
        help_text="Short code, unique within the region, e.g. NTU. Left blank, one is derived from the email domain.",
    )

    def clean(self):
        # Before validate_constraints, which runs after this in full_clean():
        # checked as typed, "ntu" would pass the uniqueness check against an
        # existing "NTU" and then fail at the database once save() uppercased it.
        super().clean()
        self.code = normalize_code(self.code)

    def save(self, *args, **kwargs):
        # Normalized here rather than only in the admin serializer: seed
        # scripts, the Django admin and bulk import all save Schools, and a
        # lowercase "ntu" stored by any of them would never match a lookup.
        self.code = normalize_code(self.code)
        if not self.code:
            # A School created without a code (tests, older scripts) gets the
            # same default the backfill migration gave the existing rows,
            # rather than '' — which the per-region unique constraint would
            # allow exactly once per region.
            taken = set(
                School.objects.filter(region_id=self.region_id)
                .exclude(pk=self.pk)
                .values_list('code', flat=True)
            )
            self.code = dedupe_code(derive_code(self.email_domain), taken)
        super().save(*args, **kwargs)

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
        constraints = [
            models.UniqueConstraint(fields=['region', 'code'], name='school_unique_code_per_region'),
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
    # Notification preferences (the account page's Notifications section).
    # On by default: it was the only behaviour before the switch existed.
    notify_new_message_email = models.BooleanField(
        default=True,
        help_text="Email the user about a chat message that arrives while they are not on the site",
    )
    # 'auto' follows site_language. See core.i18n.email_language_for.
    email_language = models.CharField(
        max_length=10,
        default='auto',
        help_text="Language for notification emails: 'auto' (follow site_language), or zh-TW / zh-HK / en",
    )
    site_language = models.CharField(
        max_length=10,
        blank=True,
        default='',
        help_text="The language the user last used the site in, as reported by the frontend",
    )
    # Set when the owner deletes their account. The row stays, stripped of
    # everything personal, because Order and Review point at it from both
    # sides: a real delete cascaded through them and took the *other* party's
    # purchase history and the ratings they had earned with it. See
    # MyProfileView.delete.
    deleted_at = models.DateTimeField(null=True, blank=True)

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

    def delete(self, using=None, keep_parents=False):
        """"Deleting" an account empties it rather than removing the row.

        Order.buyer/seller and Review.reviewer/reviewee are CASCADE from both
        sides, so a real row delete took the *counterparty's* purchase
        history and the ratings they had earned down with it — one person
        leaving erased another person's record. Everything personal goes;
        what is left is an anonymous row the surviving orders and reviews can
        still point at.

        Overridden here rather than left to the view that first needed it
        (MyProfileView.delete) so every path that deletes a User —that view,
        `manage.py shell`, a single object deleted from Django admin— goes
        through the same anonymization instead of only the one call site
        that happened to remember it.

        `using` and `keep_parents` are accepted, not honoured: this never
        performs a real delete, on any database alias, so there is nothing
        for either to apply to. Django's bulk `QuerySet.delete()` does not
        call this method at all (it deletes with raw SQL) — see
        UserAdmin.delete_queryset, which forces the admin's "Delete selected"
        action through instances one at a time so it does not bypass this.
        """
        from django.db import transaction
        from accounts.one_time_tokens import cancel_email_change
        from accounts.services import revoke_all_tokens_for_user

        with transaction.atomic(using=using):
            # One save each rather than a bulk update: post_save is what
            # bumps the listing cache generation, and a queryset update never
            # fires it, so the listings would stay in every cached feed.
            for listing in self.listings.exclude(status='removed'):
                listing.status = 'removed'
                listing.save(update_fields=['status'])

            # Campus addresses and waitlist rows are personal data with
            # nothing pointing at them; they go for real.
            self.region_verifications.all().delete()
            self.subscriptions.all().delete()
            self.socialaccount_set.all().delete()
            # School requests stay: which campuses people asked for is the
            # useful part, and it names nobody. The address they typed does.
            self.school_requests.exclude(edu_email='').update(edu_email='')

            self.email = f"deleted-{uuid.uuid4().hex}@deleted.invalid"
            self.first_name = "Deleted account"
            self.last_name = ""
            self.avatar_url = ""
            self.is_active = False
            self.deleted_at = timezone.now()
            self.set_unusable_password()
            self.save(using=using, update_fields=[
                "email", "first_name", "last_name", "avatar_url",
                "is_active", "deleted_at", "password",
            ])

        cancel_email_change(self.id)
        revoke_all_tokens_for_user(str(self.id))

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


class SchoolRequest(models.Model):
    """A user's "please support my school" report.

    Filed from the campus-email verification form when the address resolves
    to no School: until now that answer was a dead end, and the only record
    that anyone had asked for a campus was the user's own frustration. Staff
    triage these in the admin console and add the School by hand — nothing
    here creates one, because the email domain a campus really uses is not
    something the reporter can be trusted to spell (see _is_valid_edu_email
    on how far real domains stray from the region's suffix).
    """
    STATUS_PENDING = 'pending'
    STATUS_ADDED = 'added'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_ADDED, 'Added'),
        (STATUS_REJECTED, 'Rejected'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='school_requests')
    region = models.ForeignKey('core.Region', on_delete=models.PROTECT, related_name='school_requests')
    school_name = models.CharField(max_length=255)
    school_website = models.URLField(max_length=500)
    # What the user typed into the verification form, if anything. Kept so
    # staff can read the domain off it; it is not verified and never becomes
    # a RegionVerification.
    edu_email = models.EmailField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    admin_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['region', 'status', '-created_at'], name='schoolreq_region_status_idx'),
        ]
        constraints = [
            # One open request per user, region and school. The view answers a
            # repeat with the existing row; this is what still holds when two
            # submits (a double click) race past that check together.
            # Case-folded so "NTU" and "ntu" count as the same school.
            models.UniqueConstraint(
                'user', 'region', Lower('school_name'),
                condition=models.Q(status='pending'),
                name='one_pending_school_request_per_user_region_name',
            ),
        ]

    def __str__(self):
        return f"{self.school_name} ({self.region_id}, {self.status})"


class RefreshTokenRecord(models.Model):
    """One whitelisted refresh token, for accounts.token_store.PostgresTokenStore.

    A live token has rotated_at NULL. Rotation keeps the row, so a replay of
    the old token reads as "already exchanged" rather than "never issued";
    rotated_tokens holds the pair it became and is blanked by the cleanup
    cron once the grace window has passed (ADR 0001).
    """
    jti = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='refresh_tokens')
    expires_at = models.DateTimeField(db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    rotated_tokens = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # revoke_all: the user's live tokens.
            models.Index(
                fields=['user'],
                name='refresh_token_live_by_user',
                condition=models.Q(rotated_at__isnull=True),
            ),
        ]


class OneTimeToken(models.Model):
    """A single-use link mailed to a user, for accounts.one_time_tokens.

    Deleted when followed; expired rows are dropped by the cleanup cron
    (ADR 0001).
    """
    PURPOSE_CHOICES = (
        ('register', 'Registration'),
        ('edu_verify', 'Campus email verification'),
        ('password_reset', 'Password reset'),
        ('email_change', 'Email change'),
    )

    token = models.CharField(max_length=64, primary_key=True)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='one_time_tokens')
    payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # pending_email_change / cancel_email_change.
            models.Index(fields=['user', 'purpose'], name='one_time_token_user_purpose'),
        ]

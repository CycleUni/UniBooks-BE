from django.db import models
from django.conf import settings


class Category(models.Model):
    region = models.ForeignKey('core.Region', on_delete=models.CASCADE, related_name='categories')
    """Homepage college category (was hardcoded mock data).

    `title`/`description` are the canonical English values; localized values
    live in `translations`, e.g. {"zh-TW": {"title": "商管學院",
    "description": "經濟、會計、企管"}}.
    """
    slug = models.SlugField()
    title = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    translations = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['slug', 'region'], name='category_slug_region_uniq')
        ]
        constraints = [
            models.UniqueConstraint(fields=['slug', 'region'], name='category_slug_region_uniq')
        ]
        verbose_name_plural = 'categories'

    def localized(self, lang):
        from core.i18n import pick_translation
        translated = pick_translation(self.translations, lang)
        return {
            'slug': self.slug,
            'title': translated.get('title') or self.title,
            'desc': translated.get('description') or self.description,
        }

    def __str__(self):
        return self.title


class AuditEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=100)
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AuditEvent {self.kind} by {self.user}"

class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)          # ISO 4217: 'TWD', 'HKD'
    symbol = models.CharField(max_length=8)                          # 'NT$', 'HK$'
    decimal_places = models.PositiveSmallIntegerField(default=2)
    symbol_position = models.CharField(max_length=6, default='prefix',
                                       choices=[('prefix','prefix'),('suffix','suffix')])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return self.code


class Language(models.Model):
    code = models.CharField(max_length=10, primary_key=True)         # 'en', 'zh-TW', 'zh-HK'
    name = models.CharField(max_length=100)                          # 'Traditional Chinese (Hong Kong)'
    native_name = models.CharField(max_length=100)                   # '繁體中文（香港）'
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'code']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Region(models.Model):
    code = models.CharField(max_length=2, primary_key=True)          # ISO 3166-1 alpha-2: 'TW', 'HK'
    name = models.CharField(max_length=100)                          # Canonical English name, e.g., 'Taiwan'
    translations = models.JSONField(default=dict, blank=True)        # {"zh-TW": {"name": "台灣"}}
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name='regions')
    default_language = models.ForeignKey(Language, on_delete=models.PROTECT, related_name='default_for_regions')
    languages = models.ManyToManyField(Language, related_name='regions')
    timezone = models.CharField(max_length=64, default='Asia/Taipei')
    search_engines = models.JSONField(default=list)                  # ['googlebooks','openlibrary','isbnnet']
    edu_email_suffix = models.JSONField(default=list, blank=True)    # ['.edu.tw'] or ['.edu.hk', '.edu']
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'code']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def localized_name(self, lang):
        from core.i18n import pick_translation
        return pick_translation(self.translations, lang).get('name') or self.name

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from core.cache import safe_cache_delete

@receiver([post_save, post_delete], sender=Region)
@receiver([post_save, post_delete], sender=Language)
@receiver([post_save, post_delete], sender=Currency)
def invalidate_region_caches(sender, **kwargs):
    """
    Invalidate region and currency caches when data is changed.
    Ensures that modifications in the admin panel propagate immediately
    instead of waiting for the TTL to expire.
    """
    safe_cache_delete('active_regions')
    if sender == Currency:
        instance = kwargs.get('instance')
        if instance:
            safe_cache_delete(f"currency_dp_{instance.code}")

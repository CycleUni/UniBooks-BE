import logging
from django.db.models import Count
from rest_framework import views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.models import School
from core.cache import HOME_STATIC_TTL, HOME_WAITLIST_TTL
from core.i18n import resolve_language
from core.models import Category
from django.core.cache import cache
from subscriptions.models import Subscription

logger = logging.getLogger(__name__)

# The only two languages the frontend ever requests (see
# CycleUni-FE/src/app/core/i18n.service.ts) — kept in sync manually since
# `translations` fields accept arbitrary language tags, but the UI itself
# only ever renders these two, so only these two cache entries can exist.
HOME_STATIC_CACHE_LANGUAGES = ('en', 'zh-TW')


def invalidate_home_static_cache():
    """Bust HomeMetadataView's 24h schools/categories cache. Call this
    anywhere a School or Category row is created, updated, deleted, or
    bulk-imported (adminapi.views) — otherwise admin changes don't show up
    on the homepage for up to 24 hours."""
    for lang in HOME_STATIC_CACHE_LANGUAGES:
        cache.delete(f"home_static_{lang}")


class HomeMetadataView(views.APIView):
    """
    Home page metadata (schools, categories, and most wanted books).

    This view depends on `accounts.School` and `subscriptions.Subscription`. It is placed in
    the `accounts` app instead of `core` to avoid circular dependencies and keep `core`
    as an independent module. The URL remains `/api/v1/core/metadata/`.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        lang = resolve_language(request)

        # 1. Static data (Schools & Categories) cached for 24 hours
        static_cache_key = f"home_static_{lang}"
        static_data = cache.get(static_cache_key)

        if not static_data:
            schools = [
                {
                    'id': school.id,
                    'name': school.name,
                    'display_name': school.localized_name(lang),
                    'email_domain': school.email_domain,
                }
                for school in School.objects.all()
            ]

            categories = [
                category.localized(lang)
                for category in Category.objects.filter(is_active=True)
            ]

            static_data = {
                "schools": schools,
                "categories": categories,
            }
            # Long TTL is safe here only because admin edits invalidate this
            # immediately (invalidate_home_static_cache); it's a backstop, not
            # the staleness window.
            cache.set(static_cache_key, static_data, timeout=HOME_STATIC_TTL)

        # 2. Dynamic data (Most Wanted). Deliberately TTL-only in production —
        # see core.cache — so this TTL *is* the staleness window, kept short
        # while the dataset is small.
        school_name = request.query_params.get('school')
        school_id = None
        invalid_school = False
        if school_name:
            try:
                school_id = School.objects.values_list('id', flat=True).get(name=school_name)
            except School.DoesNotExist:
                invalid_school = True

        if invalid_school:
            waitlist = []
        else:
            if school_id is not None:
                waitlist_cache_key = f"home_waitlist_{school_id}"
            else:
                waitlist_cache_key = "home_waitlist_all"
            waitlist = cache.get(waitlist_cache_key)

            if waitlist is None:
                if school_id is not None:
                    top_books = Subscription.objects.filter(user__school_id=school_id).values('book_id', 'book__title', 'book__cover_url').annotate(count=Count('user')).order_by('-count')[:7]
                else:
                    top_books = Subscription.objects.values('book_id', 'book__title', 'book__cover_url').annotate(count=Count('user')).order_by('-count')[:7]
                waitlist = [
                    {
                        "title": item['book__title'],
                        "count": item['count'],
                        "book_id": item['book_id'],
                        "cover_url": item['book__cover_url'],
                    }
                    for item in top_books
                ]
                cache.set(waitlist_cache_key, waitlist, timeout=HOME_WAITLIST_TTL)

        # 3. Combine and return
        response_data = {
            "lang": lang,
            "schools": static_data["schools"],
            "categories": static_data["categories"],
            "waitlist": waitlist
        }

        return Response(response_data)

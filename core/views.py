# NOTE: HomeMetadataView has been migrated to `accounts.views` to avoid
# circular dependencies. It depends on `accounts.School` and `subscriptions.Subscription`.
# The URL remains at `/api/v1/core/metadata/` (see core/urls.py).

import logging
from django.core.cache import cache
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """Health check endpoint for load balancers and monitoring.

    Checks:
    - Database connectivity (via SELECT 1)
    - Cache connectivity (via set/get)
    - Unapplied migrations count (via MigrationExecutor)

    Returns HTTP 200 when healthy and HTTP 503 when any check fails.
    Unauthenticated and does not expose internal secrets, versions, or stack traces.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        # 1. Database check
        db_ok = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                row = cursor.fetchone()
                if row and row[0] == 1:
                    db_ok = True
        except Exception:
            logger.exception("Health check database failure")
            db_ok = False

        # 2. Cache check
        cache_ok = False
        try:
            test_key = "healthz_probe"
            test_val = "1"
            cache.set(test_key, test_val, timeout=10)
            if cache.get(test_key) == test_val:
                cache.delete(test_key)
                cache_ok = True
        except Exception:
            logger.exception("Health check cache failure")
            cache_ok = False

        # 3. Unapplied migrations check. Loading the full migration graph is
        # the expensive part of this endpoint (it imports every migration
        # module and queries django_migrations), and the answer only changes
        # on deploy — so the count is remembered for a minute. The probe is
        # unauthenticated and unthrottled; without this each hit cost a full
        # graph build.
        migrations_ok = False
        unapplied_count = None
        try:
            unapplied_count = cache.get("healthz_unapplied_migrations") if cache_ok else None
            if unapplied_count is None:
                executor = MigrationExecutor(connections["default"])
                targets = executor.loader.graph.leaf_nodes()
                plan = executor.migration_plan(targets)
                unapplied_count = len(plan)
                if cache_ok:
                    cache.set("healthz_unapplied_migrations", unapplied_count, timeout=60)
            migrations_ok = (unapplied_count == 0)
        except Exception:
            logger.exception("Health check migrations failure")
            migrations_ok = False
            unapplied_count = None

        healthy = db_ok and cache_ok and migrations_ok

        data = {
            "status": "healthy" if healthy else "unhealthy",
            "checks": {
                "database": "ok" if db_ok else "error",
                "cache": "ok" if cache_ok else "error",
                "migrations": "ok" if migrations_ok else "error",
            },
            "unapplied_migrations": unapplied_count,
        }

        http_status = (
            status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(data, status=http_status)


class RegionsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from core.models import Region
        from core.i18n import resolve_language

        lang = resolve_language(request)
        regions = (
            Region.objects.filter(is_active=True)
            .select_related('currency')
            .prefetch_related('languages')
            .order_by('sort_order', 'code')
        )
        data = []
        for r in regions:
            data.append({
                "code": r.code,
                "name": r.name,
                "localized_name": r.localized_name(lang),
                # An object, not the bare code: the frontend divides minor
                # units back to major by `decimal_places` and cannot format a
                # price without it. Returning just "TWD" left that undefined
                # and every price rendered as NaN.
                "currency": {
                    "code": r.currency.code,
                    "symbol": r.currency.symbol,
                    "decimal_places": r.currency.decimal_places,
                    "symbol_position": r.currency.symbol_position,
                },
                "languages": [lang_obj.code for lang_obj in r.languages.all()],
                "default_language": r.default_language_id,
                "timezone": r.timezone,
                "search_engines": r.search_engines,
                "edu_email_suffix": r.edu_email_suffix,
            })
        return Response(data, status=status.HTTP_200_OK)

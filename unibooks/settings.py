"""UniBooks backend — Django settings.

Configuration policy per backend-ssd §8.1 (hard rules):
- Missing required variables → raise ImproperlyConfigured at startup; no
  defaults, hardcoded values, or auto-generation.
- Development fallbacks (SQLite / LocMemCache) only with DEBUG=True, and
  always with a warning.
- Any fallback triggered while DEBUG=False → startup failure.

All environment variables are read in the "environment variable declarations"
block of this file (single auditable location); everything else must go
through django.conf.settings instead of reading os.environ directly.
"""

import os
from pathlib import Path
from datetime import timedelta

import urllib.parse
import environ

from core import conf

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

# Local development loads .env (not committed; key list in .env.example).
# Existing OS environment variables take precedence over .env (overwrite=False);
# tests/CI can set DJANGO_READ_DOT_ENV_FILE=0 to disable .env loading entirely.
if env.bool("DJANGO_READ_DOT_ENV_FILE", default=True):
    environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# =====================================================================
# Environment variable declarations (backend-ssd §8.1)
# =====================================================================

# ---- Required, no defaults: missing or blank aborts startup ----
SECRET_KEY = conf.require(env, "SECRET_KEY")
JWT_SIGNING_KEY = conf.require(env, "JWT_SIGNING_KEY")
GOOGLE_BOOKS_API_KEY = conf.require(env, "GOOGLE_BOOKS_API_KEY")

# CFEdgeChat (Cloudflare Workers realtime chat microservice): optional feature,
# disabled gracefully when unset (see messaging.views.ChatTokenView). Must
# match the CFEdgeChat Worker's EDGE_CHAT_JWT_SECRET (wrangler secret / .dev.vars).
EDGE_CHAT_JWT_SECRET = env.str("EDGE_CHAT_JWT_SECRET", default="")
EDGE_CHAT_URL = env.str("EDGE_CHAT_URL", default="http://localhost:8787")

# ISBNnet Resolver (Cloudflare Worker microservice for exact-ISBN lookups):
# local dev default, must be overridden in prod.
ISBNNET_RESOLVER_URL = env.str("ISBNNET_RESOLVER_URL", default="http://localhost:8789")

# Must match the `appId` path segment the frontend uses when it builds
# CFEdgeChat room URLs (`/ws/<app_id>/<room_id>`, `/api/<app_id>/<room_id>/...`
# — see UniBooks-FE's message.service.ts, currently the literal "unibooks").
# CFEdgeChat's ChatRoom Durable Object is keyed by `${appId}:${roomId}`, and
# now requires every room-scoped JWT to carry a matching `app_id` claim
# (strict equality, no fallback) to prevent a token minted for one app's room
# "X" from also authorizing a different app's identically-named room "X".
# Non-secret, so a default matching the frontend's current hardcoded value is
# fine here (backend-ssd §8.1 allows safe defaults for non-secret config).
EDGE_CHAT_APP_ID = env.str("EDGE_CHAT_APP_ID", default="unibooks")

# Shared secret CFEdgeChat must send in the `X-Webhook-Secret` header when
# calling back into messaging.views.EdgeChatWebhookView. Same optional-feature
# posture as EDGE_CHAT_JWT_SECRET above: unset → the webhook is disabled
# (returns 403) rather than silently accepting unauthenticated calls.
EDGE_CHAT_WEBHOOK_SECRET = env.str("EDGE_CHAT_WEBHOOK_SECRET", default="")

# Shared secret an external scheduler (e.g. Vercel Cron, since this project's
# vercel.json already deploys the whole Django app as a Python function —
# Cron just needs a GET to a path in this same deployment) must send as
# `Authorization: Bearer <CRON_SECRET>` to call cron.views.WaitlistNotifyView.
# Unset → the endpoint refuses every request rather than running unauthenticated.
CRON_SECRET = env.str("CRON_SECRET", default="")

# ---- Non-secret general configuration: safe defaults allowed ----
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
_cors_origins = env.list("CORS_ALLOWED_ORIGINS", default=[])
if "*" in _cors_origins:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOWED_ORIGINS = []
else:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = _cors_origins

# Frontend base URL: used to build links embedded in outbound email
# (email verification). Dev-friendly default matches local Angular dev server;
# production must set this explicitly.
FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:4200")

# Auto-allow frontend domain in ALLOWED_HOSTS and CORS_ALLOWED_ORIGINS
_parsed_frontend = urllib.parse.urlparse(FRONTEND_URL)
if _parsed_frontend.hostname and _parsed_frontend.hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_parsed_frontend.hostname)

# Always allow local connections in development for SSR and local testing
if DEBUG:
    ALLOWED_HOSTS = ['*']

_frontend_origin = f"{_parsed_frontend.scheme}://{_parsed_frontend.netloc}"
if not CORS_ALLOW_ALL_ORIGINS and _frontend_origin not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(_frontend_origin)

if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + [
    "ngsw-bypass",
    # Sent on every request by the frontend's ApiUrlInterceptor. Without it
    # the browser's preflight rejects the whole call, so the app cannot load
    # any data cross-origin — see core/region.py for how it is consumed.
    "x-region",
]

# Sender address for outbound transactional email. Resolved in core/conf.py
# with the same dev-fallback guard as the database and storage: it used to
# default to a domain this project has been renamed away from, so an unset
# variable sent every verification and password-reset mail from an address
# nobody owns, and nothing said so.
DEFAULT_FROM_EMAIL = conf.resolve_from_email(env, debug=DEBUG)

# One-time initial-superuser bootstrap for a fresh production deployment with
# no admin account yet (see accounts.apps.AccountsConfig._create_default_superuser,
# which only runs when DEBUG=False). Optional and unset by default — the
# feature is simply off unless both are provided; see that function for the
# "one set, one missing" fail-loud guard.
DEFAULT_SUPERUSER_EMAIL = env.str("DEFAULT_SUPERUSER_EMAIL", default="")
DEFAULT_SUPERUSER_PASSWORD = env.str("DEFAULT_SUPERUSER_PASSWORD", default="")
DEFAULT_SUPERUSER_FIRST_NAME = env.str("DEFAULT_SUPERUSER_FIRST_NAME", default="Admin")
DEFAULT_SUPERUSER_LAST_NAME = env.str("DEFAULT_SUPERUSER_LAST_NAME", default="")

# ---- Development fallbacks (DEBUG=True only; with DEBUG=False they abort startup) ----
DATABASES = {
    "default": conf.resolve_database_config(env, debug=DEBUG, base_dir=BASE_DIR),
}
CACHES = {
    "default": conf.resolve_cache_config(env, debug=DEBUG),
}
# Cloudflare R2 (S3-compatible) for listing photos / avatars; falls back to
# local FileSystemStorage under DEBUG=True only (see core/conf.py).
STORAGES = {
    "default": conf.resolve_storage_config(env, debug=DEBUG),
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# =====================================================================
# Application composition (backend-ssd §4: one Django project, apps per domain)
# =====================================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Third-party
    "rest_framework",
    "corsheaders",
    "storages",
    "anymail",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    # UniBooks domain apps (backend-ssd §4)
    "accounts",
    "catalog",
    "listings",
    "search",
    "subscriptions",
    "messaging",
    "moderation",
    "orders",
    "core",
    "adminapi",
    "cron",
    "ads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "unibooks.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Configure allauth providers to use env variables instead of DB
GOOGLE_CLIENT_ID = env.str("GOOGLE_CLIENT_ID", default="")
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': GOOGLE_CLIENT_ID,
            'secret': env.str("GOOGLE_CLIENT_SECRET"),
            'key': ''
        }
    }
}

WSGI_APPLICATION = "unibooks.wsgi.application"

# Lambda + Neon pooled connection:
# CONN_MAX_AGE=0 closes the connection after every request so the Lambda
# function doesn't hold a leaked socket past the cold-execution lifetime.
# However, a PrematureClose from Neon under load can still fail a request
# before Django has a chance to benefit from the pooled connection. The real
# fix is retry logic in lower levels:
# 1) Neon itself replays these at the pool level (no client-side code needed)
# 2) The frontend retry interceptor catches 5xx from Lambda timeouts
# 3) DATABASES['default']['OPTIONS'] below adds a statement_timeout so no
#    query blocks the Lambda function beyond Vercel's maxDuration
#
# For a busy production deployment, also consider CONN_MAX_AGE = 30 (seconds)
# — but only if the Lambda execution container visibly outlives a single
# request (e.g. when running under an always-warm provisioned instance).
# For standard Vercel Pro, keep 0. Configurable so that call can be made from
# the environment, on the deployment where it can actually be observed,
# rather than needing a code change and a redeploy to try.
CONN_MAX_AGE = env.int("CONN_MAX_AGE", default=0)

# Neon pooler rejects statement_timeout as a startup parameter and requires
# SSL. Set sslmode=require by default (overridable via POSTGRES_SSLMODE for
# local/dev databases like postgres:16-alpine that don't have SSL configured).
# For query-timeout protection, set a short connect_timeout — the frontend
# RetryInterceptor will handle the rest.
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    current_options = DATABASES["default"].get("OPTIONS", {})
    # Only merge options that we know aren't already set by the user
    DATABASES["default"]["OPTIONS"] = {
        "sslmode": env.str("POSTGRES_SSLMODE", default="require"),
        "connect_timeout": 8,
        **current_options,
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
from django.utils.translation import gettext_lazy as _
LANGUAGES = [
    ('zh-hant', _('Traditional Chinese')),
    ('zh-hk', _('Traditional Chinese (Hong Kong)')),
    ('en', _('English')),
]
DEFAULT_REGION = 'TW'
LANGUAGE_CODE = 'en'
TIME_ZONE = "Asia/Taipei"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles_build', 'static')

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email Configuration — Mailjet in real deployments; falls back to the
# console backend under DEBUG=True only (see core/conf.py resolve_email_backend_config).
EMAIL_BACKEND, ANYMAIL = conf.resolve_email_backend_config(env, debug=DEBUG)

# Custom user model
AUTH_USER_MODEL = "accounts.User"

# DRF & JWT
# Throttle rates are relaxed only when RELAX_THROTTLES is set, never by DEBUG
# alone. The two are not the same question: the test suite runs with
# DEBUG=True so the database and cache take their dev fallbacks (see
# conftest.py), and keying the rates on DEBUG made every throttling test
# assert the loosened numbers instead of the ones that ship — four of them
# failed, and the rest were silently checking nothing. Local tooling that
# needs headroom (the Playwright recorder, say) sets RELAX_THROTTLES=1 in its
# own environment; production never does, and nor do the tests.
RELAX_THROTTLES = env.bool("RELAX_THROTTLES", default=False)


def _throttle(enforced, relaxed):
    return relaxed if RELAX_THROTTLES else enforced


REST_FRAMEWORK = {
    # Normalizes DRF's own {"detail": ...} errors into this API's
    # {"error": {"code": ...}} contract — see core/exception_handler.py.
    'EXCEPTION_HANDLER': 'core.exception_handler.api_exception_handler',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    # Scoped throttles applied per-view via throttle_scope (accounts.views:
    # login/register/verification-request are brute-force / abuse targets).
    # Backed by CACHES["default"] (Upstash Redis in prod, LocMemCache in dev).
    'DEFAULT_THROTTLE_RATES': {
        'login': _throttle('5/min', '60/min'),
        'register': _throttle('10/hour', '100/hour'),
        'verify-request': _throttle('3/hour', '60/hour'),
        'search': _throttle('60/min', '600/min'),
        'listing_create': _throttle('10/hour', '1000/hour'),
        'refresh_token': _throttle('10/min', '60/min'),
        'password_change': _throttle('5/min', '60/min'),
        'password-reset-request': _throttle('3/hour', '60/hour'),
        'cron': '60/hour',
        # Listing/chat image uploads (presign, direct-proxy and delete).
        # Generous enough for a 6-photo listing plus edits, but bounded so a
        # logged-in account can't run up storage costs by looping uploads.
        'upload': _throttle('100/hour', '500/hour'),
        'ad_stats': '60/min',
        # /auth/users/<int>/ answers with a name and a join date for any id,
        # and the ids are sequential — unthrottled that is a directory of
        # every account, walkable in one pass.
        'public_profile': _throttle('30/min', '300/min'),
    },
}

SITE_ID = 1

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
)

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
# allauth >=65 reads LOGIN_METHODS / SIGNUP_FIELDS and only falls back to the
# deprecated ACCOUNT_AUTHENTICATION_METHOD / ACCOUNT_EMAIL_REQUIRED /
# ACCOUNT_USERNAME_REQUIRED when these are absent. Since they're set here,
# those three are never read — don't re-add them.
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_LOGIN_METHODS = {'email'}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=14),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'SIGNING_KEY': JWT_SIGNING_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# =====================================================================
# Security headers (SecurityMiddleware). Same DEBUG-gated posture as the
# rest of this file: forcing HTTPS/secure-cookie settings under local
# DEBUG=True development would break plain-http `manage.py runserver`, so
# the HTTPS-enforcing settings only activate with DEBUG=False (prod).
# =====================================================================
X_FRAME_OPTIONS = "DENY"

if DEBUG:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
else:
    # Lambda deployment (A3) typically sits behind a load balancer / API
    # Gateway that terminates TLS and forwards over plain HTTP internally;
    # SECURE_PROXY_SSL_HEADER lets Django trust that proxy's forwarded-proto
    # header instead of misdetecting every request as insecure.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # 1 year, with subdomains and preload — standard production HSTS posture.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

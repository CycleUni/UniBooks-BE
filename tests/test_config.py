"""Configuration smoke tests (backend-ssd §8.1; acceptance criteria B5/B6/B7).

Approach: re-execute settings.py via importlib under controlled environment
variables to verify "missing required → refuse to start", "DEBUG=True fallback
+ warning", and "DEBUG=False fallback = failure".
All environment variable values are fictitious test fakes.
"""

import importlib.util
import os
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = BASE_DIR / "unibooks" / "settings.py"

# §8.1 "required, no defaults" keys
REQUIRED_KEYS = [
    "SECRET_KEY",
    "JWT_SIGNING_KEY",
    "GOOGLE_BOOKS_API_KEY",
]

# Complete fake environment (fictitious values; contains no real secrets)
FULL_ENV = {
    "DJANGO_READ_DOT_ENV_FILE": "0",
    "SECRET_KEY": "test-only-secret-key-fake-value",
    "JWT_SIGNING_KEY": "test-only-jwt-signing-key-fake-value",
    "GOOGLE_BOOKS_API_KEY": "test-only-google-books-key-fake-value",
    "GOOGLE_CLIENT_SECRET": "test-only-google-client-secret-fake-value",
    "POSTGRES_PASSWORD": "fake_pass",
    "POSTGRES_HOST": "db.invalid",
    "POSTGRES_DATABASE": "unibooks_test",
    "POSTGRES_USER": "fake_user",
    "DB_PORT": "5432",
    "REDIS_URL": "redis://redis.invalid:6379/0",
    "R2_ACCOUNT_ID": "test-only-account-id",
    "R2_ACCESS_KEY_ID": "test-only-access-key",
    "R2_SECRET_ACCESS_KEY": "test-only-secret-key",
    "R2_BUCKET": "test-only-bucket",
    "R2_PUBLIC_URL": "https://media.example.invalid",
    "MAILJET_API_KEY": "test-only-mailjet-api-key",
    "MAILJET_SECRET_KEY": "test-only-mailjet-secret-key",
    "DEFAULT_FROM_EMAIL": "noreply@example.invalid",
    "DEBUG": "False",
}


def load_settings(env_vars):
    """Re-execute settings.py in a controlled environment (clear=True wipes existing vars)."""
    with mock.patch.dict(os.environ, env_vars, clear=True):
        spec = importlib.util.spec_from_file_location(
            "unibooks_settings_under_test", SETTINGS_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def env_without(*keys):
    return {k: v for k, v in FULL_ENV.items() if k not in keys}


# ---------------------------------------------------------------------
# B5: required, no defaults — missing raises ImproperlyConfigured,
# with no hardcoded or auto-generated values
# ---------------------------------------------------------------------


@pytest.mark.parametrize("missing_key", REQUIRED_KEYS)
def test_missing_required_key_refuses_to_start(missing_key):
    """Any missing required variable → ImproperlyConfigured at startup."""
    with pytest.raises(ImproperlyConfigured):
        load_settings(env_without(missing_key))


@pytest.mark.parametrize("empty_key", REQUIRED_KEYS)
def test_blank_required_key_refuses_to_start(empty_key):
    """A blank required variable (e.g. copied .env.example left unfilled) → also refuse to start."""
    env_vars = dict(FULL_ENV)
    env_vars[empty_key] = "  "
    with pytest.raises(ImproperlyConfigured):
        load_settings(env_vars)


def test_secrets_come_from_environment_not_hardcoded():
    """Secret values must come from the environment (proves no hardcoding or auto-generation)."""
    settings = load_settings(FULL_ENV)
    assert settings.SECRET_KEY == FULL_ENV["SECRET_KEY"]
    assert settings.JWT_SIGNING_KEY == FULL_ENV["JWT_SIGNING_KEY"]
    # The settings.py source must not contain Django's template hardcoded-secret traces
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "django-insecure" not in source
    assert "get_random_secret_key" not in source


# ---------------------------------------------------------------------
# B6: DEBUG=True development fallbacks — SQLite / LocMemCache + warning
# ---------------------------------------------------------------------


def test_debug_missing_database_url_falls_back_to_sqlite_with_warning():
    env_vars = env_without("POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_DATABASE", "POSTGRES_USER", "DB_PORT")
    env_vars["DEBUG"] = "True"
    with pytest.warns(RuntimeWarning, match="Database credentials"):
        settings = load_settings(env_vars)
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


def test_debug_missing_redis_url_falls_back_to_locmem_with_warning():
    env_vars = env_without("REDIS_URL")
    env_vars["DEBUG"] = "True"
    with pytest.warns(RuntimeWarning, match="REDIS_URL"):
        settings = load_settings(env_vars)
    assert (
        settings.CACHES["default"]["BACKEND"]
        == "django.core.cache.backends.locmem.LocMemCache"
    )


def test_debug_missing_r2_credentials_falls_back_to_filesystem_with_warning():
    env_vars = env_without("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL")
    env_vars["DEBUG"] = "True"
    with pytest.warns(RuntimeWarning, match="R2"):
        settings = load_settings(env_vars)
    assert (
        settings.STORAGES["default"]["BACKEND"]
        == "django.core.files.storage.FileSystemStorage"
    )


def test_partial_r2_config_refuses_to_start_even_under_debug():
    """A half-set R2 config (e.g. a typo'd var name) must fail loudly, not
    silently fall back to local disk in what was meant to be a real deploy."""
    env_vars = env_without("R2_PUBLIC_URL")
    env_vars["DEBUG"] = "True"
    with pytest.raises(ImproperlyConfigured, match="R2_PUBLIC_URL"):
        load_settings(env_vars)


def test_debug_missing_mailjet_credentials_falls_back_to_console_with_warning():
    env_vars = env_without("MAILJET_API_KEY", "MAILJET_SECRET_KEY")
    env_vars["DEBUG"] = "True"
    with pytest.warns(RuntimeWarning, match="Mailjet"):
        settings = load_settings(env_vars)
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"


def test_partial_mailjet_config_refuses_to_start_even_under_debug():
    env_vars = env_without("MAILJET_SECRET_KEY")
    env_vars["DEBUG"] = "True"
    with pytest.raises(ImproperlyConfigured, match="MAILJET_SECRET_KEY"):
        load_settings(env_vars)


# ---------------------------------------------------------------------
# B7: guard — any fallback triggered while DEBUG=False → startup failure
# ---------------------------------------------------------------------


def test_prod_missing_database_url_refuses_to_start():
    env_vars = env_without("POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_DATABASE", "POSTGRES_USER", "DB_PORT")
    env_vars["DEBUG"] = "False"
    with pytest.raises(ImproperlyConfigured, match="Database credentials"):
        load_settings(env_vars)


def test_prod_missing_redis_url_refuses_to_start():
    env_vars = env_without("REDIS_URL")
    env_vars["DEBUG"] = "False"
    with pytest.raises(ImproperlyConfigured, match="REDIS_URL"):
        load_settings(env_vars)


def test_prod_missing_r2_credentials_refuses_to_start():
    env_vars = env_without("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL")
    env_vars["DEBUG"] = "False"
    with pytest.raises(ImproperlyConfigured, match="R2"):
        load_settings(env_vars)


def test_prod_missing_mailjet_credentials_refuses_to_start():
    env_vars = env_without("MAILJET_API_KEY", "MAILJET_SECRET_KEY")
    env_vars["DEBUG"] = "False"
    with pytest.raises(ImproperlyConfigured, match="Mailjet"):
        load_settings(env_vars)


def test_prod_missing_from_email_refuses_to_start():
    """Unset, this used to fall back to a domain the project no longer owns,
    and every verification and reset mail went out from it."""
    env_vars = env_without("DEFAULT_FROM_EMAIL")
    env_vars["DEBUG"] = "False"
    with pytest.raises(ImproperlyConfigured, match="DEFAULT_FROM_EMAIL"):
        load_settings(env_vars)


def test_from_email_gets_a_display_name():
    settings = load_settings(FULL_ENV)
    assert settings.DEFAULT_FROM_EMAIL == '"UniBooks" <noreply@example.invalid>'

    already_named = dict(FULL_ENV, DEFAULT_FROM_EMAIL='"Books" <hi@example.invalid>')
    assert load_settings(already_named).DEFAULT_FROM_EMAIL == '"Books" <hi@example.invalid>'


def test_debug_defaults_to_false_so_bare_env_refuses_fallback():
    """DEBUG defaults to False when unset: a missing DB config must still fail startup (CI smoke scenario)."""
    env_vars = env_without("POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_DATABASE", "POSTGRES_USER", "DB_PORT", "DEBUG")
    with pytest.raises(ImproperlyConfigured):
        load_settings(env_vars)


# ---------------------------------------------------------------------
# Happy path: a complete (prod-shaped) environment triggers no fallbacks or warnings
# ---------------------------------------------------------------------


def test_dot_env_loading_enabled_does_not_override_real_env():
    """With .env loading enabled (the default), existing env vars still win (overwrite=False)."""
    env_vars = dict(FULL_ENV)
    env_vars["DJANGO_READ_DOT_ENV_FILE"] = "1"
    settings = load_settings(env_vars)
    assert settings.SECRET_KEY == FULL_ENV["SECRET_KEY"]


def test_prod_cors_defaults_to_no_cross_origin_access():
    """With DEBUG=False and no CORS_ALLOWED_ORIGINS, allow-all must be off."""
    settings = load_settings(FULL_ENV)
    assert settings.CORS_ALLOW_ALL_ORIGINS is False
    assert settings.CORS_ALLOWED_ORIGINS == ["http://localhost:4200"]


def test_cors_origins_parsed_from_env():
    env_vars = dict(FULL_ENV)
    env_vars["CORS_ALLOWED_ORIGINS"] = "https://a.example,https://b.example"
    settings = load_settings(env_vars)
    assert settings.CORS_ALLOWED_ORIGINS == ["https://a.example", "https://b.example", "http://localhost:4200"]
    assert settings.CORS_ALLOW_ALL_ORIGINS is False


def test_full_env_builds_r2_s3_storage_backend():
    settings = load_settings(FULL_ENV)  # DEBUG=False, full R2 config present
    default_storage = settings.STORAGES["default"]
    assert default_storage["BACKEND"] == "storages.backends.s3.S3Storage"
    options = default_storage["OPTIONS"]
    assert options["bucket_name"] == "test-only-bucket"
    assert options["endpoint_url"] == "https://test-only-account-id.r2.cloudflarestorage.com"
    assert options["custom_domain"] == "media.example.invalid"
    assert options["signature_version"] == "s3v4"


def test_full_env_builds_mailjet_email_backend():
    settings = load_settings(FULL_ENV)  # DEBUG=False, full Mailjet config present
    assert settings.EMAIL_BACKEND == "anymail.backends.mailjet.EmailBackend"
    assert settings.ANYMAIL["MAILJET_API_KEY"] == "test-only-mailjet-api-key"
    assert settings.ANYMAIL["MAILJET_SECRET_KEY"] == "test-only-mailjet-secret-key"


def test_redis_cache_has_socket_timeouts_configured():
    # Without an explicit socket timeout, a slow/hanging connection to a
    # serverless-hostile Redis endpoint (e.g. Upstash from a cold Vercel
    # function) blocks until the platform kills the whole request — this
    # must fail fast instead (see core/conf.py resolve_cache_config).
    settings = load_settings(FULL_ENV)  # DEBUG=False, REDIS_URL present
    cache_config = settings.CACHES["default"]
    assert cache_config["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
    options = cache_config["OPTIONS"]
    assert options["socket_timeout"] <= 5
    assert options["socket_connect_timeout"] <= 5


def test_prod_enforces_https_and_secure_cookies():
    settings = load_settings(FULL_ENV)  # DEBUG=False
    assert settings.SECURE_SSL_REDIRECT is True
    assert settings.SESSION_COOKIE_SECURE is True
    assert settings.CSRF_COOKIE_SECURE is True
    assert settings.SECURE_HSTS_SECONDS > 0
    assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert settings.X_FRAME_OPTIONS == "DENY"


def test_debug_does_not_force_https_or_secure_cookies():
    env_vars = dict(FULL_ENV)
    env_vars["DEBUG"] = "True"
    settings = load_settings(env_vars)
    assert settings.SECURE_SSL_REDIRECT is False
    assert settings.SESSION_COOKIE_SECURE is False
    assert settings.CSRF_COOKIE_SECURE is False
    assert settings.SECURE_HSTS_SECONDS == 0


def test_full_env_uses_postgres_and_redis_without_warnings():
    import warnings as warnings_module

    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error", RuntimeWarning)
        settings = load_settings(FULL_ENV)
    assert settings.DEBUG is False
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    assert (
        settings.CACHES["default"]["BACKEND"]
        == "django.core.cache.backends.redis.RedisCache"
    )
    assert settings.CACHES["default"]["LOCATION"] == FULL_ENV["REDIS_URL"]


# ---------------------------------------------------------------------
# .env.template is documentation the deploy depends on — keep it honest
# ---------------------------------------------------------------------

ENV_TEMPLATE_PATH = BASE_DIR / ".env.template"


def _template_declarations():
    """(key, value) for every KEY=... line in .env.template, comments aside."""
    pairs = []
    for line in ENV_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs.append((key.strip(), value.strip()))
    return pairs


def test_env_template_declares_each_key_once():
    """A repeated key silently takes its *last* value when the file is copied
    to .env, so a duplicate is not a tidiness issue — it decides configuration
    by line order. DEFAULT_FROM_EMAIL and FRONTEND_URL were each declared
    twice, in different sections, with different values.
    """
    keys = [key for key, _ in _template_declarations()]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicates == []


def test_env_template_lists_key_names_without_values():
    """The file's own header says so ("本檔僅列鍵名，不得含任何實際值"), and it
    matters: a value here is one a deployment inherits by copying the file
    without noticing, which is how DEFAULT_FROM_EMAIL kept pointing at a
    domain the project had been renamed away from.
    """
    with_values = sorted(key for key, value in _template_declarations() if value)
    assert with_values == []


def test_env_template_covers_every_key_settings_requires():
    """Anything settings.py refuses to start without has to be discoverable
    here, or the first sign of a missing variable is a failed deploy."""
    declared = {key for key, _ in _template_declarations()}
    for key in REQUIRED_KEYS + ["DEFAULT_FROM_EMAIL"]:
        assert key in declared, f"{key} is required by settings.py but absent from .env.template"

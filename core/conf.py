"""Configuration loading helpers (backend-ssd §8.1).

Hard rules:
- Missing security-related configuration must abort startup (raise
  ImproperlyConfigured).
- Required variables must never have defaults, hardcoded values, or
  auto-generated fallbacks.
- Only developer-convenience fallbacks (SQLite / LocMemCache) are allowed,
  and only when DEBUG=True, with an explicit warning; triggering a fallback
  with DEBUG=False aborts startup.

This module must not import Django models or anything that depends on
settings (settings.py calls into this module while loading).
"""

import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def require(env, name):
    """Read a required env var; missing or blank values abort startup (§8.1: required, no defaults)."""
    value = env.str(name)  # django-environ raises ImproperlyConfigured when missing
    if not value.strip():
        raise ImproperlyConfigured(
            f"Required environment variable {name} must not be blank; "
            f"defaults, hardcoded values, and auto-generation are forbidden (backend-ssd §8.1)."
        )
    return value


def _guard_dev_fallback(var_name, fallback_desc, extra_warning, *, debug):
    """Dev-fallback guard: with DEBUG=False a fallback aborts startup; with DEBUG=True it warns."""
    if not debug:
        raise ImproperlyConfigured(
            f"{var_name} is not set and DEBUG=False: production must not fall back to {fallback_desc}"
            f" (backend-ssd §8.1 guard)."
        )
    warnings.warn(
        f"{var_name} is not set; falling back to {fallback_desc} (local development only). {extra_warning}",
        RuntimeWarning,
        stacklevel=2,
    )


def resolve_database_config(env, *, debug, base_dir):
    """POSTGRES_* unset → fall back to SQLite (DEBUG=True only, with a warning)."""
    db_name = env.str("POSTGRES_DATABASE", default="")
    db_user = env.str("POSTGRES_USER", default="")
    db_password = env.str("POSTGRES_PASSWORD", default="")
    db_host = env.str("POSTGRES_HOST", default="")
    
    # We check the primary 4 variables to determine if Postgres is being configured.
    core_vars = [db_name, db_user, db_password, db_host]
    all_set = all(core_vars)
    any_set = any(core_vars)

    if any_set and not all_set:
        missing = [
            name for name, value in [
                ("POSTGRES_DATABASE", db_name), ("POSTGRES_USER", db_user),
                ("POSTGRES_PASSWORD", db_password), ("POSTGRES_HOST", db_host),
            ] if not value
        ]
        raise ImproperlyConfigured(
            f"Database is partially configured — missing {', '.join(missing)}. "
            f"Set all of POSTGRES_DATABASE/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_HOST, "
            f"or none of them to use the local dev fallback (backend-ssd §8.1 guard)."
        )

    if all_set:
        # DB_PORT defaults to 5432 if Postgres is configured but DB_PORT is omitted.
        db_port = env.str("DB_PORT", default="5432")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": db_name,
            "USER": db_user,
            "PASSWORD": db_password,
            "HOST": db_host,
            "PORT": db_port,
        }

    _guard_dev_fallback("Database credentials (POSTGRES_DATABASE etc.)", "SQLite (db.sqlite3)", "", debug=debug)
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(Path(base_dir) / "db.sqlite3"),
    }


def resolve_cache_config(env, *, debug):
    """REDIS_URL unset → fall back to LocMemCache (DEBUG=True only, with a warning).

    Note: production uses Upstash (REST) with separate auth/cache databases
    per A4; the REST cache backend belongs to a later core-abstraction task.
    For the skeleton phase this uses Django's built-in RedisCache config
    (lazy initialization, no connection at startup).
    """
    redis_url = env.str("REDIS_URL", default="")
    if redis_url.strip():
        return {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": redis_url,
            # Without an explicit socket timeout, a slow/hanging TCP handshake
            # to Upstash (common for a cold serverless function reconnecting
            # from a fresh container) blocks indefinitely — on Vercel that
            # means the whole function runs past its execution time limit and
            # gets killed by the platform, which the browser sees as a bare
            # net::ERR_FAILED with no HTTP response at all (intermittent,
            # "works on refresh"). A short timeout makes a bad connection
            # fail fast as a normal exception instead.
            "OPTIONS": {
                "socket_timeout": 3,
                "socket_connect_timeout": 3,
                "retry_on_timeout": True,
            },
        }
    _guard_dev_fallback(
        "REDIS_URL",
        "LocMemCache",
        "Rate limiting and the JWT whitelist are only valid within a single process; local development only.",
        debug=debug,
    )
    return {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}


def resolve_storage_config(env, *, debug):
    """R2 credentials unset → fall back to local FileSystemStorage (DEBUG=True only, with a warning).

    Cloudflare R2 is S3-compatible, so this uses django-storages' S3Boto3Storage
    pointed at R2's endpoint (`https://<account_id>.r2.cloudflarestorage.com`)
    rather than a separate storage backend. R2 buckets have no default public
    URL (unlike S3 with static website hosting), so R2_PUBLIC_URL — a custom
    domain or the bucket's r2.dev public development URL — is required
    alongside the credentials for uploaded files to be browser-reachable.
    """
    account_id = env.str("R2_ACCOUNT_ID", default="")
    access_key = env.str("R2_ACCESS_KEY_ID", default="")
    secret_key = env.str("R2_SECRET_ACCESS_KEY", default="")
    bucket = env.str("R2_BUCKET", default="")
    public_url = env.str("R2_PUBLIC_URL", default="")

    all_set = all([account_id, access_key, secret_key, bucket, public_url])
    any_set = any([account_id, access_key, secret_key, bucket, public_url])

    if any_set and not all_set:
        # A half-configured R2 setup (e.g. a typo'd var name) must fail loudly
        # rather than silently falling back to local disk storage in what was
        # meant to be a real deployment.
        missing = [
            name for name, value in [
                ("R2_ACCOUNT_ID", account_id), ("R2_ACCESS_KEY_ID", access_key),
                ("R2_SECRET_ACCESS_KEY", secret_key), ("R2_BUCKET", bucket),
                ("R2_PUBLIC_URL", public_url),
            ] if not value
        ]
        raise ImproperlyConfigured(
            f"R2 storage is partially configured — missing {', '.join(missing)}. "
            f"Set all of R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET/R2_PUBLIC_URL, "
            f"or none of them to use the local dev fallback (backend-ssd §8.1 guard)."
        )

    if all_set:
        return {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": access_key,
                "secret_key": secret_key,
                "bucket_name": bucket,
                "endpoint_url": f"https://{account_id}.r2.cloudflarestorage.com",
                "custom_domain": public_url.replace("https://", "").replace("http://", "").rstrip("/"),
                "url_protocol": "https:" if public_url.startswith("https://") else "http:",
                # R2 only supports the newer SigV4 signing, and path-style
                # addressing (R2 doesn't support virtual-hosted-style hitting
                # <bucket>.<account>.r2.cloudflarestorage.com).
                "addressing_style": "path",
                "signature_version": "s3v4",
                "region_name": "auto",
                "file_overwrite": False,
                "default_acl": None,
            },
        }

    _guard_dev_fallback(
        "R2 storage credentials (R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET/R2_PUBLIC_URL)",
        "local FileSystemStorage under MEDIA_ROOT",
        "Uploaded files will not survive redeploys and are not served from a CDN; local development only.",
        debug=debug,
    )
    return {"BACKEND": "django.core.files.storage.FileSystemStorage"}


def resolve_from_email(env, *, debug):
    """DEFAULT_FROM_EMAIL unset → noreply@localhost (DEBUG=True only).

    Wrapped in the display-name form unless the value already carries one, so
    the address renders as `"UniBooks" <...>` in a mail client.
    """
    raw = env.str("DEFAULT_FROM_EMAIL", default="").strip()
    if not raw:
        _guard_dev_fallback(
            "DEFAULT_FROM_EMAIL",
            "noreply@localhost",
            "Outbound mail would name an address nobody owns.",
            debug=debug,
        )
        raw = "noreply@localhost"
    return raw if "<" in raw else f'"UniBooks" <{raw}>'


def resolve_email_backend_config(env, *, debug):
    """MAILJET_API_KEY/MAILJET_SECRET_KEY unset → fall back to the console
    backend (DEBUG=True only, with a warning) — no email is actually sent,
    it's just printed to stdout. Returns (EMAIL_BACKEND, ANYMAIL dict).
    """
    api_key = env.str("MAILJET_API_KEY", default="")
    secret_key = env.str("MAILJET_SECRET_KEY", default="")

    all_set = bool(api_key) and bool(secret_key)
    any_set = bool(api_key) or bool(secret_key)

    if any_set and not all_set:
        missing = [
            name for name, value in [("MAILJET_API_KEY", api_key), ("MAILJET_SECRET_KEY", secret_key)]
            if not value
        ]
        raise ImproperlyConfigured(
            f"Mailjet email is partially configured — missing {', '.join(missing)}. "
            f"Set both MAILJET_API_KEY and MAILJET_SECRET_KEY, or neither to use the "
            f"local dev fallback (backend-ssd §8.1 guard)."
        )

    if all_set:
        return "anymail.backends.mailjet.EmailBackend", {
            "MAILJET_API_KEY": api_key,
            "MAILJET_SECRET_KEY": secret_key,
            "TRACK_CLICKS": False,
            "TRACK_OPENS": False,
        }

    _guard_dev_fallback(
        "Mailjet credentials (MAILJET_API_KEY/MAILJET_SECRET_KEY)",
        "the console email backend (prints instead of sending)",
        "Verification emails will not actually be delivered; local development only.",
        debug=debug,
    )
    return "django.core.mail.backends.console.EmailBackend", {}

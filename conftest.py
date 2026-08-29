"""Global pytest setup: inject test environment variables before Django initializes.

Red line: everything below is a fictitious test fake — never put real secrets
or personal data here (backend-ssd §8.1, agent-charter §6).
"""

import os

_TEST_ENV_DEFAULTS = {
    # Declared here (not in pyproject.toml) so fake env vars are injected before settings load
    "DJANGO_SETTINGS_MODULE": "unibooks.settings",
    # Tests must not read the developer's local .env, keeping results reproducible
    "DJANGO_READ_DOT_ENV_FILE": "0",
    # Fake values for the §8.1 required variables (fictitious)
    "SECRET_KEY": "test-only-secret-key-fake-value",
    "JWT_SIGNING_KEY": "test-only-jwt-signing-key-fake-value",
    "GOOGLE_BOOKS_API_KEY": "test-only-google-books-key-fake-value",
    "GOOGLE_CLIENT_SECRET": "test-only-google-client-secret-fake-value",
    # Tests run with DEBUG=True so POSTGRES_* / REDIS_URL use the dev fallbacks
    "DEBUG": "True",
}

for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

# DJANGO_SETTINGS_MODULE is intentionally absent from the pytest ini (see the
# pyproject.toml note), so pytest-django does not initialize Django itself;
# initialize the app registry explicitly after injecting the env vars above.
# (E402: the import must come after the env var injection — deliberate.)
import django  # noqa: E402

django.setup()

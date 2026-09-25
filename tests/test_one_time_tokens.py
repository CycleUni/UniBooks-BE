"""Mailed single-use links in Postgres, and the legacy cache fallback
(ADR 0001)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from accounts import one_time_tokens
from accounts.models import OneTimeToken

User = get_user_model()

PASSWORD = "test-only-password-123"
NEW_PASSWORD = "a-brand-new-strong-password-9"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="links@example.com", first_name="Link", last_name="User", password=PASSWORD
    )


def _confirm_reset(api, token, password=NEW_PASSWORD):
    return api.post(
        "/api/v1/auth/password/reset/confirm/",
        {"token": token, "new_password": password},
        content_type="application/json",
    )


def test_a_reset_link_is_a_row_and_nothing_is_cached(api, user):
    api.post("/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json")

    row = OneTimeToken.objects.get(user=user, purpose=one_time_tokens.PASSWORD_RESET)
    assert cache.get(f"password-reset:{row.token}") is None

    assert _confirm_reset(api, row.token).status_code == 200
    assert not OneTimeToken.objects.filter(token=row.token).exists()
    assert _confirm_reset(api, row.token, "another-strong-password-9").status_code == 400


def test_a_rejected_password_leaves_the_link_usable(api, user):
    token = one_time_tokens.issue(one_time_tokens.PASSWORD_RESET, user.id, one_time_tokens.PASSWORD_RESET_TTL)

    assert _confirm_reset(api, token, "123").status_code == 400
    assert _confirm_reset(api, token).status_code == 200


def test_an_expired_link_is_refused(api, user):
    token = one_time_tokens.issue(one_time_tokens.PASSWORD_RESET, user.id, one_time_tokens.PASSWORD_RESET_TTL)
    OneTimeToken.objects.filter(token=token).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert _confirm_reset(api, token).status_code == 400


def test_a_link_only_works_for_its_own_purpose(api, user):
    token = one_time_tokens.issue(one_time_tokens.REGISTER, user.id, one_time_tokens.REGISTER_TTL)

    assert _confirm_reset(api, token).status_code == 400


def test_a_new_email_change_request_supersedes_the_previous_link(user):
    first = one_time_tokens.issue_email_change(user.id, "first@example.com")
    second = one_time_tokens.issue_email_change(user.id, "second@example.com")

    assert one_time_tokens.read(one_time_tokens.EMAIL_CHANGE, first) is None
    assert one_time_tokens.read(one_time_tokens.EMAIL_CHANGE, second)["email"] == "second@example.com"
    assert one_time_tokens.pending_email_change(user.id) == "second@example.com"


def test_purge_drops_only_expired_links(user):
    old = one_time_tokens.issue(one_time_tokens.REGISTER, user.id, one_time_tokens.REGISTER_TTL)
    OneTimeToken.objects.filter(token=old).update(expires_at=timezone.now() - timedelta(seconds=1))
    live = one_time_tokens.issue(one_time_tokens.REGISTER, user.id, one_time_tokens.REGISTER_TTL)

    assert one_time_tokens.purge() == 1
    assert list(OneTimeToken.objects.values_list("token", flat=True)) == [live]


# ---------------------------------------------------------------------
# Links mailed before the switch
# ---------------------------------------------------------------------


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_cache_era_reset_link_still_works_once(api, user):
    cache.set("password-reset:old-link", {"user_id": user.id}, timeout=3600)

    assert _confirm_reset(api, "old-link").status_code == 200
    assert cache.get("password-reset:old-link") is None
    assert _confirm_reset(api, "old-link", "another-strong-password-9").status_code == 400


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_cache_era_registration_link_still_activates(api, db):
    pending = User.objects.create_user(
        email="pending@example.com", first_name="Pending", last_name="User", password=PASSWORD, is_active=False
    )
    cache.set("register-verify:old-link", {"user_id": pending.id}, timeout=86400)

    resp = api.post("/api/v1/auth/verify-registration/", {"token": "old-link"}, content_type="application/json")

    assert resp.status_code == 200
    pending.refresh_from_db()
    assert pending.is_active is True


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_cache_era_pending_email_change_is_visible_and_cancellable(user):
    cache.set("email-change:old-link", {"user_id": user.id, "email": "moved@example.com"}, timeout=3600)
    cache.set(f"email-change-pending:{user.id}", {"email": "moved@example.com", "token": "old-link"}, timeout=3600)

    assert one_time_tokens.pending_email_change(user.id) == "moved@example.com"

    one_time_tokens.cancel_email_change(user.id)

    assert one_time_tokens.pending_email_change(user.id) is None
    assert one_time_tokens.read(one_time_tokens.EMAIL_CHANGE, "old-link") is None


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=False)
def test_without_the_fallback_cache_era_links_are_refused(api, user):
    cache.set("password-reset:old-link", {"user_id": user.id}, timeout=3600)

    assert _confirm_reset(api, "old-link").status_code == 400

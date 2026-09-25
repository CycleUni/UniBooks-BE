"""The refresh-token whitelist in Postgres, and the legacy cache fallback
(ADR 0001)."""

import time
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import Client, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import RefreshTokenRecord
from accounts.services import (
    REFRESH_ROTATION_GRACE,
    REFRESH_TOKEN_LIFETIME,
    issue_tokens,
    purge_token_store,
    refresh_token_is_whitelisted,
    revoke_all_tokens_for_user,
)
from accounts.token_store import (
    LegacyCacheTokenStore,
    MigratingTokenStore,
    PostgresTokenStore,
    get_token_store,
)

User = get_user_model()

PASSWORD = "test-only-password-123"


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
        email="store@example.com", first_name="Store", last_name="User", password=PASSWORD
    )


@pytest.fixture(params=[True, False], ids=["with-legacy-fallback", "postgres-only"])
def fallback(request):
    with override_settings(AUTH_LEGACY_CACHE_FALLBACK=request.param):
        yield request.param


def _refresh(api, refresh_token):
    return api.post("/api/v1/auth/refresh/", {"refresh": refresh_token}, content_type="application/json")


def _jti(refresh_token):
    return RefreshToken(refresh_token)["jti"]


def _legacy_pair(user):
    """A token pair whitelisted the way it was before ADR 0001: cache keys only."""
    refresh = RefreshToken.for_user(user)
    refresh.set_exp(lifetime=REFRESH_TOKEN_LIFETIME)
    jti, user_id = refresh["jti"], str(user.id)
    timeout = int(REFRESH_TOKEN_LIFETIME.total_seconds())
    cache.set(f"jwt:rt:{jti}", user_id, timeout=timeout)
    cache.set(f"jwt:user:{user_id}", [*cache.get(f"jwt:user:{user_id}", []), jti], timeout=timeout)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


# ---------------------------------------------------------------------
# The contract, with and without the fallback
# ---------------------------------------------------------------------


def test_issued_tokens_are_rows_and_nothing_is_cached(fallback, api, user):
    tokens = issue_tokens(user)
    jti = _jti(tokens["refresh"])
    header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}

    row = RefreshTokenRecord.objects.get(jti=jti)
    assert row.user_id == user.id
    assert row.rotated_at is None

    rotated = _refresh(api, tokens["refresh"]).json()
    api.post("/api/v1/auth/logout/", {"refresh": rotated["refresh"]}, content_type="application/json", **header)
    for key in (f"jwt:rt:{jti}", f"jwt:rt:{_jti(rotated['refresh'])}", f"jwt:user:{user.id}"):
        assert cache.get(key) is None


def test_refresh_rotates_and_the_old_token_becomes_a_rotation_record(fallback, api, user):
    tokens = issue_tokens(user)
    resp = _refresh(api, tokens["refresh"])

    assert resp.status_code == 200
    assert resp.json()["refresh"] != tokens["refresh"]
    old = RefreshTokenRecord.objects.get(jti=_jti(tokens["refresh"]))
    assert old.rotated_at is not None
    assert old.rotated_tokens == resp.json()


def test_concurrent_refresh_within_grace_gets_the_same_pair(fallback, api, user):
    tokens = issue_tokens(user)
    first = _refresh(api, tokens["refresh"])
    second = _refresh(api, tokens["refresh"])

    assert second.status_code == 200
    assert second.json() == first.json()


def test_a_stale_replay_is_refused_without_logging_out_other_devices(fallback, api, user):
    phone = issue_tokens(user)
    laptop = issue_tokens(user)
    assert _refresh(api, phone["refresh"]).status_code == 200

    RefreshTokenRecord.objects.filter(jti=_jti(phone["refresh"])).update(
        rotated_at=timezone.now() - timedelta(hours=1)
    )

    replay = _refresh(api, phone["refresh"])
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "auth.errTokenRevoked"
    assert _refresh(api, laptop["refresh"]).status_code == 200


def test_an_unknown_token_is_401(fallback, api, user):
    tokens = issue_tokens(user)
    RefreshTokenRecord.objects.filter(jti=_jti(tokens["refresh"])).delete()

    assert _refresh(api, tokens["refresh"]).status_code == 401


def test_an_expired_row_is_treated_as_unknown(fallback, api, user):
    tokens = issue_tokens(user)
    RefreshTokenRecord.objects.filter(jti=_jti(tokens["refresh"])).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    assert _refresh(api, tokens["refresh"]).status_code == 401


def test_simultaneous_logins_are_all_revocable(fallback, api, user):
    """The cache kept a per-user list rewritten on every login, so two logins
    at once could drop each other and "log out everywhere" missed one."""
    devices = [issue_tokens(user) for _ in range(3)]
    header = {"HTTP_AUTHORIZATION": f"Bearer {devices[0]['access']}"}

    api.post("/api/v1/auth/logout/", {"all_devices": True}, content_type="application/json", **header)

    for pair in devices:
        assert _refresh(api, pair["refresh"]).status_code == 401


def test_revoke_all_keeps_rotation_records(fallback, api, user):
    """A rotated token must still read as "already exchanged" afterwards, or
    its replay would look like an unknown token rather than a replay."""
    tokens = issue_tokens(user)
    _refresh(api, tokens["refresh"])

    revoke_all_tokens_for_user(user.id)

    assert RefreshTokenRecord.objects.filter(jti=_jti(tokens["refresh"]), rotated_at__isnull=False).exists()


def test_an_owner_mismatch_revokes_the_real_owners_sessions(fallback, user):
    other = User.objects.create_user(
        email="other@example.com", first_name="Other", last_name="User", password=PASSWORD
    )
    tokens = issue_tokens(user)

    assert refresh_token_is_whitelisted(_jti(tokens["refresh"]), str(other.id)) is False
    assert not RefreshTokenRecord.objects.filter(user=user, rotated_at__isnull=True).exists()


def test_a_database_outage_on_refresh_is_503_not_401(fallback, api, user):
    tokens = issue_tokens(user)

    with mock.patch.object(PostgresTokenStore, "lookup", side_effect=OperationalError("db gone")):
        resp = _refresh(api, tokens["refresh"])

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "auth.errSessionStoreUnavailable"
    assert resp["Retry-After"] == "2"
    assert _refresh(api, tokens["refresh"]).status_code == 200


def test_a_rotation_that_cannot_be_recorded_leaves_the_old_token_working(fallback, api, user):
    tokens = issue_tokens(user)

    with mock.patch.object(PostgresTokenStore, "mark_rotated", side_effect=OperationalError("db gone")):
        assert _refresh(api, tokens["refresh"]).status_code == 503

    assert _refresh(api, tokens["refresh"]).status_code == 200


def test_login_is_503_when_the_token_cannot_be_recorded(fallback, api, user):
    with mock.patch.object(PostgresTokenStore, "record", side_effect=OperationalError("db gone")):
        resp = api.post(
            "/api/v1/auth/token/",
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        )

    assert resp.status_code == 503
    assert "refresh" not in resp.json()


# ---------------------------------------------------------------------
# Tokens issued before the switch
# ---------------------------------------------------------------------


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_cache_era_token_still_refreshes_and_is_adopted(api, user):
    tokens = _legacy_pair(user)

    resp = _refresh(api, tokens["refresh"])

    assert resp.status_code == 200
    old = RefreshTokenRecord.objects.get(jti=_jti(tokens["refresh"]))
    assert old.rotated_at is not None
    assert RefreshTokenRecord.objects.get(jti=_jti(resp.json()["refresh"])).rotated_at is None


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_cache_era_rotation_record_still_serves_the_grace_pair(api, user):
    tokens = _legacy_pair(user)
    handed_back = {"access": "a", "refresh": "r"}
    cache.set(
        f"jwt:rt:{_jti(tokens['refresh'])}",
        {"user_id": str(user.id), "tokens": handed_back, "rotated_at": time.time()},
        timeout=3600,
    )

    resp = _refresh(api, tokens["refresh"])

    assert resp.status_code == 200
    assert resp.json() == handed_back


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_logout_everywhere_also_revokes_cache_era_tokens(api, user):
    old = _legacy_pair(user)
    new = issue_tokens(user)

    revoke_all_tokens_for_user(user.id)

    assert _refresh(api, old["refresh"]).status_code == 401
    assert _refresh(api, new["refresh"]).status_code == 401


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_legacy_read_failure_is_503_not_a_sign_out(api, user):
    tokens = _legacy_pair(user)

    with mock.patch.object(LegacyCacheTokenStore, "lookup", side_effect=ConnectionError("redis timeout")):
        assert _refresh(api, tokens["refresh"]).status_code == 503

    assert _refresh(api, tokens["refresh"]).status_code == 200


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=True)
def test_a_legacy_revocation_failure_does_not_fail_logout(user):
    with mock.patch.object(LegacyCacheTokenStore, "revoke_all", side_effect=ConnectionError("redis timeout")):
        MigratingTokenStore().revoke_all(str(user.id))  # must not raise


@override_settings(AUTH_LEGACY_CACHE_FALLBACK=False)
def test_without_the_fallback_cache_era_tokens_are_unknown(api, user):
    tokens = _legacy_pair(user)

    assert _refresh(api, tokens["refresh"]).status_code == 401


# ---------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------


def test_purge_drops_expired_rows_and_blanks_stale_token_pairs(api, user):
    expired = issue_tokens(user)
    RefreshTokenRecord.objects.filter(jti=_jti(expired["refresh"])).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    rotated = issue_tokens(user)
    _refresh(api, rotated["refresh"])
    RefreshTokenRecord.objects.filter(jti=_jti(rotated["refresh"])).update(
        rotated_at=timezone.now() - REFRESH_ROTATION_GRACE - timedelta(seconds=1)
    )
    fresh = issue_tokens(user)

    purge_token_store()

    assert not RefreshTokenRecord.objects.filter(jti=_jti(expired["refresh"])).exists()
    stale = RefreshTokenRecord.objects.get(jti=_jti(rotated["refresh"]))
    assert stale.rotated_tokens is None
    assert stale.rotated_at is not None  # still recognisable as a replay
    assert RefreshTokenRecord.objects.filter(jti=_jti(fresh["refresh"])).exists()


def test_purge_keeps_the_pair_inside_the_grace_window(api, user):
    tokens = issue_tokens(user)
    _refresh(api, tokens["refresh"])

    purge_token_store()

    assert RefreshTokenRecord.objects.get(jti=_jti(tokens["refresh"])).rotated_tokens is not None


# ---------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------


@pytest.mark.parametrize("enabled, cls", [(True, MigratingTokenStore), (False, PostgresTokenStore)])
def test_the_fallback_setting_chooses_the_store(enabled, cls):
    with override_settings(AUTH_LEGACY_CACHE_FALLBACK=enabled):
        assert type(get_token_store()) is cls

"""API tests for the accounts app: register, login, verify, refresh, logout, profile."""

from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

from accounts.services import (
    REFRESH_ROTATION_GRACE,
    REFRESH_TOKEN_LIFETIME,
    issue_tokens,
    revoke_all_tokens_for_user,
    verify_and_revoke_refresh_token,
)
from catalog.models import Book
from listings.models import Listing
from subscriptions.models import Subscription

User = get_user_model()

PASSWORD = "test-only-password-123"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="user@example.com", first_name="Test", last_name="User", password=PASSWORD
    )


@pytest.fixture
def auth_header(user):
    tokens = issue_tokens(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield


@pytest.fixture
def ntu_school(db):
    from accounts.models import School

    return School.objects.create(region_id='TW', email_domain="ntu.edu.tw", name="National Taiwan University")
    cache.clear()


# ---------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------


def test_register_success(api, db):
    resp = api.post(
        "/api/v1/auth/register/",
        {"email": "new@example.com", "password": PASSWORD, "first_name": "New", "last_name": "User"},
        content_type="application/json",
    )
    assert resp.status_code == 201
    assert User.objects.filter(email="new@example.com").exists()


def test_register_stores_email_fully_lowercased(api, db):
    # Django's normalize_email only lowercases the domain part — the local
    # part must also be normalized here, or this exact account becomes
    # unreachable by a plain-lowercase login/password-reset attempt later.
    api.post(
        "/api/v1/auth/register/",
        {"email": "MixedCase3@Example.com", "password": PASSWORD, "first_name": "Mixed", "last_name": "Case"},
        content_type="application/json",
    )
    assert User.objects.filter(email="mixedcase3@example.com").exists()


def test_register_rejects_case_variant_duplicate_email(api, db):
    api.post(
        "/api/v1/auth/register/",
        {"email": "Dup@example.com", "password": PASSWORD, "first_name": "First", "last_name": "User"},
        content_type="application/json",
    )
    resp = api.post(
        "/api/v1/auth/register/",
        {"email": "dup@example.com", "password": PASSWORD, "first_name": "Second", "last_name": "User"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["fields"]["email"][0] == "acct.errEmailTaken"


def test_register_validation_error(api, db):
    resp = api.post(
        "/api/v1/auth/register/",
        {"email": "not-an-email", "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errValidation"


def test_register_creates_inactive_account(api, db):
    """New self-registered accounts are inactive until the emailed link is
    clicked — Google sign-in (a separate code path) is exempt from this."""
    api.post(
        "/api/v1/auth/register/",
        {"email": "pending@example.com", "password": PASSWORD, "first_name": "Pending", "last_name": "User"},
        content_type="application/json",
    )
    user = User.objects.get(email="pending@example.com")
    assert user.is_active is False


def test_register_sends_verification_email(api, db, mailoutbox):
    api.post(
        "/api/v1/auth/register/",
        {"email": "pending@example.com", "password": PASSWORD, "first_name": "Pending", "last_name": "User"},
        content_type="application/json",
    )
    assert len(mailoutbox) == 1
    assert "pending@example.com" in mailoutbox[0].to[0]
    assert "type=register" in mailoutbox[0].body


def test_login_blocked_before_registration_verification(api, db):
    api.post(
        "/api/v1/auth/register/",
        {"email": "pending@example.com", "password": PASSWORD, "first_name": "Pending", "last_name": "User"},
        content_type="application/json",
    )
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": "pending@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.errAccountDisabled"


def test_verify_registration_activates_account_and_logs_in(api, db):
    verify_token = "fixed-register-verify-token"
    with mock.patch("accounts.views.uuid.uuid4", return_value=verify_token):
        api.post(
            "/api/v1/auth/register/",
            {"email": "pending@example.com", "password": PASSWORD, "first_name": "Pending", "last_name": "User"},
            content_type="application/json",
        )
    user = User.objects.get(email="pending@example.com")
    assert user.is_active is False

    resp = api.post(
        "/api/v1/auth/verify-registration/", {"token": verify_token}, content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access" in body and "refresh" in body

    user.refresh_from_db()
    assert user.is_active is True

    # A used/deleted token cannot be replayed
    resp = api.post(
        "/api/v1/auth/verify-registration/", {"token": verify_token}, content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errInvalidToken"


def test_verify_registration_rejects_invalid_token(api, db):
    resp = api.post(
        "/api/v1/auth/verify-registration/", {"token": "not-a-real-token"}, content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errInvalidToken"


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------


def test_login_success(api, user):
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access"] and body["refresh"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "user@example.com"},
        {"password": PASSWORD},
        {},
    ],
)
def test_login_missing_fields(api, user, payload):
    resp = api.post("/api/v1/auth/token/", payload, content_type="application/json")
    assert resp.status_code == 400


def test_login_wrong_password(api, user):
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": "wrong-password"},
        content_type="application/json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errInvalidCredentials"


def test_login_unknown_email_same_response_as_wrong_password(api, db):
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": "nobody@example.com", "password": "whatever"},
        content_type="application/json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errInvalidCredentials"


def test_login_succeeds_with_different_email_case(api, db):
    # Django's normalize_email only lowercases the domain part, not the local
    # part, so an account registered with any capitalization must still be
    # findable by a plain-lowercase (or any-case) retype at login — nobody
    # expects email lookups to be case-sensitive.
    User.objects.create_user(
        email="MixedCase@example.com", first_name="Mixed", last_name="Case", password=PASSWORD
    )
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": "mixedcase@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_login_disabled_account(api, user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    resp = api.post(
        "/api/v1/auth/token/",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.errAccountDisabled"


# ---------------------------------------------------------------------
# Refresh (rotation + grace period)
# ---------------------------------------------------------------------


def test_refresh_succeeds_repeatedly_without_forcing_relogin(api, user):
    # Regression test for the actual bug behind "logged out constantly,
    # locally and in production": simplejwt's RefreshToken.for_user() always
    # stores the user_id claim as str(user.id) (see
    # rest_framework_simplejwt.tokens.RefreshToken.for_user), but
    # issue_tokens used to cache the raw int user.id. RefreshTokenView reads
    # the claim back as a string and compared it against that cached int —
    # `"1" != 1` on literally every refresh — which verify_and_revoke_refresh_
    # token treated as proof of token reuse/theft and revoked every session.
    # Chained refreshes (as any real session does many times over its life)
    # must keep succeeding, not fail on the very first one.
    tokens = issue_tokens(user)
    for _ in range(3):
        resp = api.post(
            "/api/v1/auth/refresh/",
            {"refresh": tokens["refresh"]},
            content_type="application/json",
        )
        assert resp.status_code == 200
        tokens = resp.json()


def test_cache_miss_on_refresh_does_not_revoke_other_sessions(api, user):
    # A bare "not found" for one device's refresh token (evicted, or a local
    # dev-server restart wiping LocMemCache) must fail only that one refresh
    # — it must NOT cascade into logging out every other device/tab, which
    # was the second half of the same bug: any cache hiccup was treated as
    # definitive proof of theft.
    tokens_a = issue_tokens(user)  # device A
    tokens_b = issue_tokens(user)  # device B, still valid

    from rest_framework_simplejwt.tokens import RefreshToken
    jti_a = RefreshToken(tokens_a["refresh"])["jti"]
    cache.delete(f"jwt:rt:{jti_a}")  # simulate an operational cache miss for device A only

    resp_a = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens_a["refresh"]},
        content_type="application/json",
    )
    assert resp_a.status_code == 401

    # Device B must be completely unaffected.
    resp_b = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens_b["refresh"]},
        content_type="application/json",
    )
    assert resp_b.status_code == 200


def test_refresh_rotates_tokens(api, user):
    tokens = issue_tokens(user)
    resp = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    assert resp.status_code == 200
    new_pair = resp.json()
    assert new_pair["refresh"] != tokens["refresh"]


def test_rotation_keeps_user_token_set_alive_for_full_lifetime(user):
    # Regression test: verify_and_revoke_refresh_token used to rewrite
    # jwt:user:{id} via cache.set() with no explicit timeout, silently
    # falling back to Django's cache default (300s) instead of the 14-day
    # refresh-token lifetime. That let the tracking set expire long before
    # the individual jwt:rt:{jti} entries it's meant to enumerate, so
    # revoke_all_tokens_for_user() (log out all devices / password reset)
    # would see an empty list and revoke nothing still-active elsewhere.
    from rest_framework_simplejwt.tokens import RefreshToken

    tokens_a = issue_tokens(user)
    issue_tokens(user)  # a second device/session for the same user

    jti_a = RefreshToken(tokens_a["refresh"])["jti"]

    with mock.patch("accounts.services.cache.set", wraps=cache.set) as set_spy:
        # user_id is compared against the whitelist as a string, matching
        # simplejwt's own str(user.id) claim (see issue_tokens) — not the
        # raw int PK.
        assert verify_and_revoke_refresh_token(jti_a, str(user.id)) is True

    user_set_calls = [
        call for call in set_spy.call_args_list
        if call.args[0] == f"jwt:user:{user.id}"
    ]
    assert user_set_calls, "expected jwt:user:{id} to be rewritten on rotation"
    for call in user_set_calls:
        timeout = call.kwargs.get("timeout") if "timeout" in call.kwargs else (
            call.args[2] if len(call.args) > 2 else None
        )
        assert timeout is not None
        assert timeout > 300  # must outlive Django's cache default
        assert timeout <= int(REFRESH_TOKEN_LIFETIME.total_seconds())


def test_refresh_concurrent_reuse_within_grace_returns_same_pair(api, user):
    tokens = issue_tokens(user)
    first = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    second = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_refresh_after_grace_expiry_is_revoked(api, user):
    tokens = issue_tokens(user)
    assert (
        api.post(
            "/api/v1/auth/refresh/",
            {"refresh": tokens["refresh"]},
            content_type="application/json",
        ).status_code
        == 200
    )
    # Simulate grace expiry by clearing the grace entry
    from rest_framework_simplejwt.tokens import RefreshToken

    jti = RefreshToken(tokens["refresh"])["jti"]
    cache.delete(f"jwt:rt:{jti}")

    resp = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errTokenRevoked"


def test_refresh_missing_and_invalid_token(api, db):
    assert (
        api.post("/api/v1/auth/refresh/", {}, content_type="application/json").status_code
        == 400
    )
    resp = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": "not-a-jwt"},
        content_type="application/json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errInvalidToken"


def test_refresh_disabled_account(api, user):
    tokens = issue_tokens(user)
    user.is_active = False
    user.save(update_fields=["is_active"])
    resp = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_grace_period_constant_is_short():
    assert REFRESH_ROTATION_GRACE.total_seconds() <= 300


# ---------------------------------------------------------------------
# Token store outages: our failure, not the caller's session
# ---------------------------------------------------------------------


class _FlakyTokenStore:
    """Stands in for the cache inside accounts.services only — DRF's throttling
    keeps the real one — and fails the operations a test names, the way an
    Upstash timeout on a cold function does."""

    def __init__(self, fail_get=None, fail_set=None):
        self._fail_get = fail_get or (lambda key: False)
        self._fail_set = fail_set or (lambda key: False)

    def get(self, key, default=None):
        if self._fail_get(key):
            raise ConnectionError("redis timeout")
        return cache.get(key, default)

    def set(self, key, value, timeout=None):
        if self._fail_set(key):
            raise ConnectionError("redis timeout")
        return cache.set(key, value, timeout=timeout)

    def delete(self, key):
        return cache.delete(key)


def _refresh(api, refresh_token):
    return api.post("/api/v1/auth/refresh/", {"refresh": refresh_token}, content_type="application/json")


def _jti(refresh_token):
    from rest_framework_simplejwt.tokens import RefreshToken
    return RefreshToken(refresh_token)["jti"]


def test_refresh_answers_503_not_401_when_the_token_store_is_unreachable(api, user):
    # The production sign-outs: the whitelist read timed out, was folded into
    # "not found", and the client was told its session was over.
    tokens = issue_tokens(user)
    flaky = _FlakyTokenStore(fail_get=lambda key: key.startswith("jwt:rt:"))

    with mock.patch("accounts.services.cache", flaky):
        resp = _refresh(api, tokens["refresh"])

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "auth.errSessionStoreUnavailable"
    assert resp["Retry-After"] == "2"

    # Nothing about the token changed; once the store is back it just works.
    assert _refresh(api, tokens["refresh"]).status_code == 200


def test_a_rotation_that_cannot_record_the_new_pair_leaves_the_old_token_working(api, user):
    # The old flow deleted the old token's entry first. Failing the write
    # after that left the client holding a token the server had forgotten, so
    # the retry was a 401 and the visitor was signed out anyway.
    tokens = issue_tokens(user)
    old_key = f"jwt:rt:{_jti(tokens['refresh'])}"
    flaky = _FlakyTokenStore(fail_set=lambda key: key.startswith("jwt:rt:") and key != old_key)

    with mock.patch("accounts.services.cache", flaky):
        assert _refresh(api, tokens["refresh"]).status_code == 503

    retried = _refresh(api, tokens["refresh"])
    assert retried.status_code == 200
    assert retried.json()["refresh"] != tokens["refresh"]


def test_a_rotation_that_cannot_record_the_rotation_leaves_the_old_token_working(api, user):
    tokens = issue_tokens(user)
    old_key = f"jwt:rt:{_jti(tokens['refresh'])}"
    flaky = _FlakyTokenStore(fail_set=lambda key: key == old_key)

    with mock.patch("accounts.services.cache", flaky):
        assert _refresh(api, tokens["refresh"]).status_code == 503

    assert _refresh(api, tokens["refresh"]).status_code == 200


def test_a_genuinely_unknown_token_is_still_401(api, user):
    # The 503 path must not swallow the real answer: evicted or never issued.
    tokens = issue_tokens(user)
    cache.delete(f"jwt:rt:{_jti(tokens['refresh'])}")

    resp = _refresh(api, tokens["refresh"])
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errTokenRevoked"


def test_login_answers_503_rather_than_issue_a_pair_that_cannot_refresh(api, user):
    flaky = _FlakyTokenStore(fail_set=lambda key: key.startswith("jwt:rt:"))

    with mock.patch("accounts.services.cache", flaky):
        resp = api.post(
            "/api/v1/auth/token/",
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        )

    assert resp.status_code == 503
    assert "refresh" not in resp.json()


def test_issuing_tokens_never_drops_other_devices_from_the_revocation_list(user):
    # A failed read of jwt:user:{id} used to degrade to [] and be written back,
    # silently removing every other device from what "log out all devices"
    # and password reset revoke.
    from accounts.services import TokenStoreUnavailable

    first = issue_tokens(user)
    user_key = f"jwt:user:{user.id}"
    flaky = _FlakyTokenStore(fail_get=lambda key: key == user_key)

    with mock.patch("accounts.services.cache", flaky):
        with pytest.raises(TokenStoreUnavailable):
            issue_tokens(user)

    assert cache.get(user_key) == [_jti(first["refresh"])]


def test_a_registration_link_survives_a_token_store_outage(api, db):
    new_user = User.objects.create_user(
        email="fresh@example.com", first_name="Fresh", last_name="User", password=PASSWORD, is_active=False
    )
    cache.set("register-verify:link-token", {"user_id": new_user.id}, 3600)
    flaky = _FlakyTokenStore(fail_set=lambda key: key.startswith("jwt:rt:"))

    with mock.patch("accounts.services.cache", flaky):
        first = api.post("/api/v1/auth/verify-registration/", {"token": "link-token"}, content_type="application/json")
    assert first.status_code == 503

    # Same link, store back: it still works.
    again = api.post("/api/v1/auth/verify-registration/", {"token": "link-token"}, content_type="application/json")
    assert again.status_code == 200
    assert "refresh" in again.json()


# ---------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------


def test_logout_revokes_refresh_token(api, user, auth_header):
    tokens = issue_tokens(user)
    resp = api.post(
        "/api/v1/auth/logout/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200
    # The revoked token can no longer refresh
    resp = api.post(
        "/api/v1/auth/refresh/",
        {"refresh": tokens["refresh"]},
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_logout_all_devices(api, user, auth_header):
    tokens_a = issue_tokens(user)
    tokens_b = issue_tokens(user)
    resp = api.post(
        "/api/v1/auth/logout/",
        {"all_devices": True},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200
    for pair in (tokens_a, tokens_b):
        assert (
            api.post(
                "/api/v1/auth/refresh/",
                {"refresh": pair["refresh"]},
                content_type="application/json",
            ).status_code
            == 401
        )


def test_logout_with_garbage_token_still_succeeds(api, user, auth_header):
    resp = api.post(
        "/api/v1/auth/logout/",
        {"refresh": "garbage"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200


def test_revoke_all_tokens_service(user):
    tokens = issue_tokens(user)
    revoke_all_tokens_for_user(user.id)
    from rest_framework_simplejwt.tokens import RefreshToken

    jti = RefreshToken(tokens["refresh"])["jti"]
    assert cache.get(f"jwt:rt:{jti}") is None


# ---------------------------------------------------------------------
# Edu email verification
# ---------------------------------------------------------------------


def test_request_verification_requires_auth(api, db):
    resp = api.post(
        "/api/v1/auth/verify/request/",
        {"edu_email": "student@ntu.edu.tw"},
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_request_verification_rejects_non_edu_email(api, user, auth_header):
    resp = api.post(
        "/api/v1/auth/verify/request/",
        {"edu_email": "student@gmail.com"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "acct.errEduEmail"


def test_request_verification_rejects_email_taken_by_other_account(api, user, auth_header, db, ntu_school):
    User.objects.create_user(
        email="taken@ntu.edu.tw", first_name="Other", last_name="User", password=PASSWORD
    )
    resp = api.post(
        "/api/v1/auth/verify/request/",
        {"edu_email": "taken@ntu.edu.tw"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "acct.errEmailTaken"


# Named "english_by_default" until 2026-08-29, while asserting the Chinese
# subject on the line below — the name described behaviour the test had
# never checked, and would have led anyone reconciling the two to change
# the code rather than the name.
def test_request_verification_email_uses_region_default_language(api, user, auth_header, mailoutbox, ntu_school):
    resp = api.post(
        "/api/v1/auth/verify/request/",
        {"edu_email": "student@ntu.edu.tw"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200
    assert len(mailoutbox) == 1
    assert mailoutbox[0].subject == "UniBooks 學生信箱驗證"


def test_request_verification_email_zh_tw_via_lang_param(api, user, auth_header, mailoutbox, ntu_school):
    resp = api.post(
        "/api/v1/auth/verify/request/?lang=zh-TW",
        {"edu_email": "student@ntu.edu.tw"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200
    assert len(mailoutbox) == 1
    assert mailoutbox[0].subject == "UniBooks 學生信箱驗證"


def test_verification_full_flow(api, user, auth_header, ntu_school):
    # Pin the verification token so the test can use it like the emailed link would
    token = "fixed-test-token"
    with mock.patch("accounts.views.uuid.uuid4", return_value=token):
        resp = api.post(
            "/api/v1/auth/verify/request/",
            {"edu_email": "student@ntu.edu.tw"},
            content_type="application/json",
            **auth_header,
        )
    assert resp.status_code == 200

    resp = api.post(
        "/api/v1/auth/verify/", {"token": token}, content_type="application/json"
    )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.is_verified_in('TW')
    # user.school is now accessible via the verification
    verification = user.region_verifications.filter(region_id='TW', is_active=True).first()
    assert verification is not None
    assert verification.school == ntu_school
    assert verification.edu_email == "student@ntu.edu.tw"

    # The token is single-use
    resp = api.post(
        "/api/v1/auth/verify/", {"token": token}, content_type="application/json"
    )
    assert resp.status_code == 400


def test_verify_missing_or_invalid_token(api, db):
    assert (
        api.post("/api/v1/auth/verify/", {}, content_type="application/json").status_code
        == 400
    )
    assert (
        api.post(
            "/api/v1/auth/verify/",
            {"token": "nonexistent"},
            content_type="application/json",
        ).status_code
        == 400
    )


# ---------------------------------------------------------------------
# Profile (/auth/me/)
# ---------------------------------------------------------------------


def test_my_profile_includes_listings_and_subscriptions(api, user, auth_header):
    book = Book.objects.create(region_id='TW', isbn13="9781111111111", title="Profile Book", source="manual")
    Listing.objects.create(region_id='TW', currency_id='TWD', 
        book=book, seller=user, price=120, condition="new", status="active"
    )
    Subscription.objects.create(region_id='TW', user=user, book=book)

    resp = api.get("/api/v1/auth/me/", **auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == user.email
    # myListings is paginated (MyProfileView.get); mySubscriptions is not.
    assert len(body["myListings"]["results"]) == 1
    assert body["myListings"]["results"][0]["book_title"] == "Profile Book"
    assert len(body["mySubscriptions"]) == 1
    assert body["mySubscriptions"][0]["isbn"] == "9781111111111"


def test_my_profile_counts_every_listing_not_just_the_first_page(api, user, auth_header):
    book = Book.objects.create(region_id='TW', isbn13="9781111111112", title="Many Copies", source="manual")
    for _ in range(21):
        Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=user, price=100, condition="new", status="active")
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=user, price=100, condition="new", status="sold")
    Listing.objects.create(region_id='HK', currency_id='HKD', book=book, seller=user, price=100, condition="new", status="active")

    body = api.get("/api/v1/auth/me/?q=nothing-matches", **auth_header).json()
    assert body["myListings"]["results"] == []
    # Every status the seller's own page offers as a tab, plus the total.
    assert body["myListingCounts"] == {
        "active": 21, "reserved": 0, "sold": 1, "removed": 0, "all": 22,
    }


def _listing(user, book, **kwargs):
    return Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=user,
        condition="new", **kwargs,
    )


@pytest.fixture
def listing_shelf(user):
    """One listing per status, at prices that order differently from their age."""
    book = Book.objects.create(region_id='TW', isbn13="9781111111113", title="Shelf Book", source="manual")
    return {
        'active': _listing(user, book, price=300, status="active"),
        'reserved': _listing(user, book, price=100, status="reserved"),
        'sold': _listing(user, book, price=500, status="sold"),
        'removed': _listing(user, book, price=200, status="removed"),
    }


def test_my_listings_filter_by_status(api, auth_header, listing_shelf):
    body = api.get("/api/v1/auth/me/?status=sold", **auth_header).json()
    assert [l["id"] for l in body["myListings"]["results"]] == [str(listing_shelf['sold'].id)]
    assert body["myListings"]["count"] == 1
    # The tab labels keep counting the whole shelf, not the filtered view.
    assert body["myListingCounts"] == {"active": 1, "reserved": 1, "sold": 1, "removed": 1, "all": 4}


def test_my_listings_ignore_an_unknown_status(api, auth_header, listing_shelf):
    body = api.get("/api/v1/auth/me/?status=banana", **auth_header).json()
    assert body["myListings"]["count"] == 4


def test_my_listings_sorting(api, auth_header, listing_shelf):
    def prices(query):
        return [l["price"] for l in api.get(f"/api/v1/auth/me/?{query}", **auth_header).json()["myListings"]["results"]]

    newest = prices("")                      # created last first
    assert newest == prices("sort=newest")
    assert newest[0] == 200                  # the removed one was created last
    assert prices("sort=oldest") == list(reversed(newest))
    assert prices("sort=price_asc") == [100, 200, 300, 500]
    assert prices("sort=price_desc") == [500, 300, 200, 100]
    assert prices("sort=banana") == newest   # unknown sort falls back


def test_my_listings_status_and_search_together(api, auth_header, listing_shelf):
    body = api.get("/api/v1/auth/me/?status=active&q=Shelf", **auth_header).json()
    assert body["myListings"]["count"] == 1
    assert api.get("/api/v1/auth/me/?status=active&q=nothing", **auth_header).json()["myListings"]["count"] == 0


def test_my_profile_requires_auth(api, db):
    assert api.get("/api/v1/auth/me/").status_code == 401


def test_my_profile_localizes_school_name(api, user, auth_header):
    from accounts.models import School
    from accounts.models import School, RegionVerification

    school = School.objects.create(region_id='TW', 
        email_domain="i18n.edu.tw",
        name="Localized University",
        translations={"zh-TW": {"name": "在地化大學"}},
    )
    RegionVerification.objects.create(user=user, region_id='TW', school=school, edu_email=user.email, verified_at='2024-01-01T00:00:00Z', is_active=True)

    assert api.get("/api/v1/auth/me/", **auth_header).json()["school_name"] == "在地化大學"
    assert (
        api.get("/api/v1/auth/me/?lang=zh-TW", **auth_header).json()["school_name"]
        == "在地化大學"
    )
    # Untranslated language falls back to the region default language
    assert (
        api.get("/api/v1/auth/me/?lang=ja", **auth_header).json()["school_name"]
        == "在地化大學"
    )


# ---------------------------------------------------------------------
# Password reset (logged-out "forgot password" flow)
# ---------------------------------------------------------------------


def test_request_password_reset_sends_email_for_known_user(api, user, mailoutbox):
    resp = api.post(
        "/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordResetSent"
    assert len(mailoutbox) == 1
    assert user.email in mailoutbox[0].to[0]


def test_password_reset_email_links_to_the_page_that_sets_the_password(api, user, mailoutbox):
    api.post("/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json")
    body = mailoutbox[0].body
    # /reset-password is no frontend route and lands on the homepage.
    assert f"{settings.FRONTEND_URL}/forgot-password?token=" in body
    assert "/reset-password" not in body


@pytest.mark.parametrize("headers, subject, absent", [
    ({"HTTP_X_REGION": "TW"}, "UniBooks 密碼重設", "Password Reset"),
    # Hong Kong used to fall through to English: only zh-TW had a branch.
    ({"HTTP_X_REGION": "HK"}, "UniBooks 重設密碼", "Password Reset"),
    ({"HTTP_X_REGION": "TW", "HTTP_ACCEPT_LANGUAGE": "en"}, "UniBooks Password Reset", "密碼"),
])
def test_password_reset_email_is_written_in_the_requests_language(api, user, mailoutbox, headers, subject, absent):
    api.post("/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json", **headers)
    mail = mailoutbox[0]
    assert mail.subject == subject
    assert absent not in mail.body


def test_request_password_reset_sends_email_regardless_of_input_case(api, db, mailoutbox):
    # Regression test for the actual bug behind "forgot password doesn't
    # work, no email arrives" in production: this view always returns the
    # same generic success response whether or not the account exists (by
    # design, so it can't be used to probe registered emails) — which meant
    # a case mismatch between the stored account email and whatever case the
    # user retyped was completely silent. Nothing looked broken; the email
    # was simply never sent because the exact-match lookup found no account.
    User.objects.create_user(
        email="MixedCase2@example.com", first_name="Mixed", last_name="Case", password=PASSWORD
    )
    resp = api.post(
        "/api/v1/auth/password/reset/request/", {"email": "mixedcase2@example.com"}, content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordResetSent"
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to[0] == "MixedCase2@example.com"


def test_request_password_reset_same_response_for_unknown_email(api, db, mailoutbox):
    resp = api.post(
        "/api/v1/auth/password/reset/request/", {"email": "nobody@example.com"}, content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordResetSent"
    assert len(mailoutbox) == 0


def test_request_password_reset_skips_google_linked_account(api, user, mailoutbox):
    from allauth.socialaccount.models import SocialAccount

    SocialAccount.objects.create(user=user, provider='google', uid='fake-google-uid')

    resp = api.post(
        "/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordResetSent"
    assert len(mailoutbox) == 0


def test_confirm_password_reset_changes_password_and_revokes_sessions(api, user):
    from unittest import mock as _mock

    old_tokens = issue_tokens(user)
    reset_token = "fixed-reset-token"
    with _mock.patch("accounts.views.uuid.uuid4", return_value=reset_token):
        api.post(
            "/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json",
        )

    resp = api.post(
        "/api/v1/auth/password/reset/confirm/",
        {"token": reset_token, "new_password": "a-brand-new-strong-password-9"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordResetSuccess"

    user.refresh_from_db()
    assert user.check_password("a-brand-new-strong-password-9")

    # The old refresh token must be revoked — a password reset should end
    # every existing session, not just the one that requested it.
    refresh_resp = api.post(
        "/api/v1/auth/refresh/", {"refresh": old_tokens["refresh"]}, content_type="application/json",
    )
    assert refresh_resp.status_code == 401

    # The token is single-use
    resp2 = api.post(
        "/api/v1/auth/password/reset/confirm/",
        {"token": reset_token, "new_password": "another-strong-password-9"},
        content_type="application/json",
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "auth.errInvalidToken"


def test_confirm_password_reset_rejects_invalid_token(api, db):
    resp = api.post(
        "/api/v1/auth/password/reset/confirm/",
        {"token": "not-a-real-token", "new_password": "a-strong-password-9"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errInvalidToken"


def test_confirm_password_reset_rejects_weak_password(api, user):
    from unittest import mock as _mock

    reset_token = "fixed-reset-token-weak"
    with _mock.patch("accounts.views.uuid.uuid4", return_value=reset_token):
        api.post(
            "/api/v1/auth/password/reset/request/", {"email": user.email}, content_type="application/json",
        )

    resp = api.post(
        "/api/v1/auth/password/reset/confirm/",
        {"token": reset_token, "new_password": "123"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errValidation"

def test_auto_verify_edu_email_different_region(api, user, db, setup_regions):
    from accounts.models import School, RegionVerification
    tw_school = School.objects.create(name="NTU", email_domain="ntu.edu.tw", region_id="TW")
    hk_school = School.objects.create(name="HKU", email_domain="hku.edu.hk", region_id="HK")
    
    RegionVerification.objects.create(user=user, region_id="TW", school=tw_school, edu_email="test@ntu.edu.tw", is_active=True)
    
    user.email = "test@hku.edu.hk"
    user.save()
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp = api.post("/api/v1/auth/verify/auto/", content_type="application/json", **auth_header)
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.verifySuccess"
    
    assert user.region_verifications.filter(region_id="HK", is_active=True).exists()
    assert user.region_verifications.filter(region_id="TW", is_active=True).exists()

def test_unbind_edu_email_specific_region(api, user, db, setup_regions):
    from accounts.models import School, RegionVerification
    tw_school = School.objects.create(name="NTU", email_domain="ntu.edu.tw", region_id="TW")
    hk_school = School.objects.create(name="HKU", email_domain="hku.edu.hk", region_id="HK")
    
    RegionVerification.objects.create(user=user, region_id="TW", school=tw_school, edu_email="test@ntu.edu.tw", is_active=True)
    RegionVerification.objects.create(user=user, region_id="HK", school=hk_school, edu_email="test@hku.edu.hk", is_active=True)
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp = api.post("/api/v1/auth/verify/unbind/", HTTP_X_REGION="HK", content_type="application/json", **auth_header)
    assert resp.status_code == 200
    
    assert not user.region_verifications.filter(region_id="HK", is_active=True).exists()
    assert user.region_verifications.filter(region_id="TW", is_active=True).exists()

def test_user_serializer_region_specific(api, user, db, setup_regions):
    from accounts.models import School, RegionVerification
    from django.utils import timezone
    tw_school = School.objects.create(name="NTU", email_domain="ntu.edu.tw", region_id="TW")
    hk_school = School.objects.create(name="HKU", email_domain="hku.edu.hk", region_id="HK")
    
    t_now = timezone.now()
    RegionVerification.objects.create(user=user, region_id="TW", school=tw_school, edu_email="test@ntu.edu.tw", is_active=True, verified_at=t_now)
    RegionVerification.objects.create(user=user, region_id="HK", school=hk_school, edu_email="test@hku.edu.hk", is_active=True, verified_at=t_now)
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp_tw = api.get("/api/v1/auth/me/", HTTP_X_REGION="TW", content_type="application/json", **auth_header)
    assert resp_tw.json()["is_verified"] is True
    
    resp_hk = api.get("/api/v1/auth/me/", HTTP_X_REGION="HK", content_type="application/json", **auth_header)
    assert resp_hk.json()["is_verified"] is True
    
    pass
    
    
    pass
    

# Password removal
def test_remove_password_success(api, user):
    from allauth.socialaccount.models import SocialAccount
    SocialAccount.objects.create(user=user, provider='google', uid='12345')
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    assert user.has_usable_password()
    resp = api.post(
        "/api/v1/auth/password/remove/", 
        {"password": PASSWORD}, 
        content_type="application/json", 
        **auth_header
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.passwordRemoved"
    
    user.refresh_from_db()
    assert not user.has_usable_password()
    
    # Must be reversible
    resp_change = api.post(
        "/api/v1/auth/password/", 
        {"new_password": "new-password-123"}, 
        content_type="application/json", 
        **auth_header
    )
    assert resp_change.status_code == 200
    user.refresh_from_db()
    assert user.has_usable_password()

def test_remove_password_wrong_password(api, user):
    from allauth.socialaccount.models import SocialAccount
    SocialAccount.objects.create(user=user, provider='google', uid='12345')
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp = api.post(
        "/api/v1/auth/password/remove/", 
        {"password": "wrong-password"}, 
        content_type="application/json", 
        **auth_header
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errInvalidPassword"
    
def test_remove_password_no_google_linked(api, user):
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp = api.post(
        "/api/v1/auth/password/remove/", 
        {"password": PASSWORD}, 
        content_type="application/json", 
        **auth_header
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errNoGoogleLinked"

def test_remove_password_is_staff_rejected(api, user):
    from allauth.socialaccount.models import SocialAccount
    SocialAccount.objects.create(user=user, provider='google', uid='12345')
    user.is_staff = True
    user.save()
    
    tokens = issue_tokens(user)
    auth_header = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access']}"}
    
    resp = api.post(
        "/api/v1/auth/password/remove/", 
        {"password": PASSWORD}, 
        content_type="application/json", 
        **auth_header
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errStaffCannotRemovePassword"


# ---------------------------------------------------------------------
# Notification settings (account page: Notifications)
# ---------------------------------------------------------------------

NOTIFICATIONS_URL = "/api/v1/auth/me/notifications/"


def test_notification_settings_require_sign_in(api, db):
    assert api.get(NOTIFICATIONS_URL).status_code == 401
    assert api.patch(NOTIFICATIONS_URL, {"new_message_email": False}, content_type="application/json").status_code == 401


def test_new_message_email_is_on_by_default(api, user, auth_header):
    # It was the only behaviour before the switch existed.
    resp = api.get(NOTIFICATIONS_URL, **auth_header)
    assert resp.status_code == 200
    assert resp.json() == {"new_message_email": True, "email_language": "auto", "site_language": ""}


def test_new_message_email_can_be_turned_off_and_on(api, user, auth_header):
    resp = api.patch(NOTIFICATIONS_URL, {"new_message_email": False}, content_type="application/json", **auth_header)
    assert resp.status_code == 200
    assert resp.json()["new_message_email"] is False
    user.refresh_from_db()
    assert user.notify_new_message_email is False

    resp = api.patch(NOTIFICATIONS_URL, {"new_message_email": True}, content_type="application/json", **auth_header)
    assert resp.json()["new_message_email"] is True
    user.refresh_from_db()
    assert user.notify_new_message_email is True


def test_notification_settings_reject_a_non_boolean(api, user, auth_header):
    resp = api.patch(NOTIFICATIONS_URL, {"new_message_email": "sometimes"}, content_type="application/json", **auth_header)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.errValidation"
    user.refresh_from_db()
    assert user.notify_new_message_email is True


def test_notification_settings_only_change_the_signed_in_user(api, user, auth_header):
    other = User.objects.create_user(email="other-notify@example.com", first_name="O", last_name="T", password=PASSWORD)

    api.patch(NOTIFICATIONS_URL, {"new_message_email": False}, content_type="application/json", **auth_header)

    other.refresh_from_db()
    assert other.notify_new_message_email is True


def test_notification_settings_ignore_unknown_fields(api, user, auth_header):
    # A PATCH to this endpoint must not become a way to write other columns.
    resp = api.patch(
        NOTIFICATIONS_URL,
        {"new_message_email": False, "is_staff": True, "email": "attacker@example.com"},
        content_type="application/json",
        **auth_header,
    )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.is_staff is False
    assert user.email == "user@example.com"


# ---------------------------------------------------------------------
# Email language (Notifications) and the language the site was last used in
# ---------------------------------------------------------------------

SITE_LANGUAGE_URL = "/api/v1/auth/me/site-language/"


@pytest.mark.parametrize("choice", ["en", "zh-HK", "zh-TW", "auto"])
def test_email_language_can_be_set(api, user, auth_header, choice):
    resp = api.patch(NOTIFICATIONS_URL, {"email_language": choice}, content_type="application/json", **auth_header)
    assert resp.status_code == 200
    assert resp.json()["email_language"] == choice
    user.refresh_from_db()
    assert user.email_language == choice


def test_email_language_refuses_a_language_there_is_no_email_for(api, user, auth_header):
    resp = api.patch(NOTIFICATIONS_URL, {"email_language": "fr"}, content_type="application/json", **auth_header)
    assert resp.status_code == 400
    user.refresh_from_db()
    assert user.email_language == "auto"


def test_site_language_is_not_writable_through_notification_settings(api, user, auth_header):
    api.patch(NOTIFICATIONS_URL, {"site_language": "en"}, content_type="application/json", **auth_header)
    user.refresh_from_db()
    assert user.site_language == ""


def test_site_language_requires_sign_in(api, db):
    assert api.put(SITE_LANGUAGE_URL, {"language": "en"}, content_type="application/json").status_code == 401


def test_site_language_is_recorded_and_shown(api, user, auth_header):
    resp = api.put(SITE_LANGUAGE_URL, {"language": "zh-HK"}, content_type="application/json", **auth_header)
    assert resp.status_code == 204
    user.refresh_from_db()
    assert user.site_language == "zh-HK"

    # Reported back where the frontend reads the profile, so it can skip
    # reporting a language that has not changed.
    assert api.get("/api/v1/auth/me/", **auth_header).json()["site_language"] == "zh-HK"
    assert api.get(NOTIFICATIONS_URL, **auth_header).json()["site_language"] == "zh-HK"


@pytest.mark.parametrize("value", ["fr", "", None, "zh"])
def test_site_language_refuses_an_unsupported_language(api, user, auth_header, value):
    resp = api.put(SITE_LANGUAGE_URL, {"language": value}, content_type="application/json", **auth_header)
    assert resp.status_code == 400
    user.refresh_from_db()
    assert user.site_language == ""


def test_email_language_prefers_the_explicit_choice_then_the_site_language_then_the_region(user):
    from core.i18n import email_language_for
    from core.models import Region

    hk = Region.objects.get(code="HK")
    assert email_language_for(user, hk) == "zh-HK"          # nothing known: the region's default

    user.site_language = "en"
    assert email_language_for(user, hk) == "en"              # auto: the language last used

    user.email_language = "zh-TW"
    assert email_language_for(user, hk) == "zh-TW"           # an explicit choice wins

    user.email_language, user.site_language = "auto", ""
    assert email_language_for(user, None) == "en"            # nothing at all: the default


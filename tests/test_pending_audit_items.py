"""Tests for the design-level findings the 2026-09 review deferred: the ones
that needed a decision rather than a patch.

Red line: every credential below is a fictitious test fake.
"""

import time

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import RegionVerification, School
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from orders.models import Order, Review

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return Client()


def _user(email, **extra):
    return User.objects.create_user(
        email=email, first_name="Te", last_name="St", password=PASSWORD, **extra
    )


def _verified(email, region='TW'):
    u = _user(email)
    RegionVerification.objects.update_or_create(
        user=u, region_id=region, defaults={'edu_email': email, 'verified_at': timezone.now()}
    )
    return u


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _token_from_pending(user):
    return cache.get(f"email-change-pending:{user.id}")['token']


# ---------------------------------------------------------------------
# Changing the sign-in email now has to be proven by reading mail sent to it
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_email_change_is_not_applied_until_the_new_address_confirms(api):
    user = _user("before@example.com")

    resp = api.patch(
        "/api/v1/auth/me/", {"email": "after@example.com"},
        content_type="application/json", **bearer(user),
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "acct.emailChangePending"

    user.refresh_from_db()
    assert user.email == "before@example.com"
    # The link goes to the address being claimed, not the one on file.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["after@example.com"]

    resp = api.post(
        "/api/v1/auth/email/change/confirm/", {"token": _token_from_pending(user)},
        content_type="application/json",
    )
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.email == "after@example.com"


@pytest.mark.django_db
def test_a_confirmation_token_works_once(api):
    user = _user("once@example.com")
    api.patch(
        "/api/v1/auth/me/", {"email": "moved@example.com"},
        content_type="application/json", **bearer(user),
    )
    token = _token_from_pending(user)

    assert api.post("/api/v1/auth/email/change/confirm/", {"token": token},
                    content_type="application/json").status_code == 200
    replay = api.post("/api/v1/auth/email/change/confirm/", {"token": token},
                      content_type="application/json")
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "acct.errEmailChangeTokenInvalid"


@pytest.mark.django_db
def test_an_address_taken_while_the_link_sat_unread_is_refused(api):
    user = _user("slow@example.com")
    api.patch(
        "/api/v1/auth/me/", {"email": "contested@example.com"},
        content_type="application/json", **bearer(user),
    )
    token = _token_from_pending(user)
    _user("contested@example.com")

    resp = api.post("/api/v1/auth/email/change/confirm/", {"token": token},
                    content_type="application/json")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "acct.errEmailTaken"
    user.refresh_from_db()
    assert user.email == "slow@example.com"


@pytest.mark.django_db
def test_a_campus_domain_added_in_the_meantime_is_refused(api):
    """The edu check runs again at confirm time, not only when the link is
    sent: a school can be imported in the hour the mail sits unread, and a
    campus address is what /verify/auto/ trusts as proof of enrolment."""
    user = _user("plain@example.com")
    api.patch(
        "/api/v1/auth/me/", {"email": "me@newcampus.example"},
        content_type="application/json", **bearer(user),
    )
    token = _token_from_pending(user)

    School.objects.create(email_domain="newcampus.example", name="New Campus", region_id='TW')

    resp = api.post("/api/v1/auth/email/change/confirm/", {"token": token},
                    content_type="application/json")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "acct.errEduEmailChangeNotAllowed"
    user.refresh_from_db()
    assert user.email == "plain@example.com"


@pytest.mark.django_db
def test_pending_change_is_visible_and_cancellable(api):
    user = _user("pending@example.com")
    api.patch(
        "/api/v1/auth/me/", {"email": "elsewhere@example.com"},
        content_type="application/json", **bearer(user),
    )
    token = _token_from_pending(user)

    profile = api.get("/api/v1/auth/me/", **bearer(user))
    assert profile.json()["pending_email"] == "elsewhere@example.com"

    assert api.post("/api/v1/auth/email/change/cancel/", **bearer(user)).status_code == 200
    assert api.get("/api/v1/auth/me/", **bearer(user)).json()["pending_email"] is None
    # The link that was already in the mailbox stops working too.
    assert api.post("/api/v1/auth/email/change/confirm/", {"token": token},
                    content_type="application/json").status_code == 400


# ---------------------------------------------------------------------
# Deleting an account no longer deletes the other party's record
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_deleting_an_account_keeps_the_counterparty_history(api):
    seller = _verified("leaver@example.com")
    buyer = _verified("stayer@example.com")
    book = Book.objects.create(region_id='TW', isbn13="9789999999901", title="B", source="manual")
    listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new",
    )
    order = Order.objects.create(
        region_id='TW', currency_id='TWD', listing=listing, buyer=buyer, seller=seller,
        status='completed', total_amount=100,
    )
    review = Review.objects.create(order=order, reviewer=buyer, reviewee=seller, rating=5)

    resp = api.delete("/api/v1/auth/me/", **bearer(seller))
    assert resp.status_code == 204

    # The buyer's purchase and the rating they wrote both survive.
    assert Order.objects.filter(pk=order.pk).exists()
    assert Review.objects.filter(pk=review.pk).exists()

    seller.refresh_from_db()
    assert seller.is_active is False
    assert seller.deleted_at is not None
    assert seller.email.endswith("@deleted.invalid")
    assert "leaver@example.com" not in seller.email
    assert seller.has_usable_password() is False
    assert seller.region_verifications.count() == 0
    listing.refresh_from_db()
    assert listing.status == 'removed'


@pytest.mark.django_db
def test_a_deleted_account_cannot_sign_in_again(api):
    user = _user("gone@example.com")
    assert api.delete("/api/v1/auth/me/", **bearer(user)).status_code == 204

    resp = api.post(
        "/api/v1/auth/token/", {"email": "gone@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------
# A rotated refresh token is only handed back inside the grace window
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_concurrent_refresh_still_gets_the_rotated_pair(api):
    user = _user("tabs@example.com")
    tokens = issue_tokens(user)

    first = api.post("/api/v1/auth/refresh/", {"refresh": tokens['refresh']},
                     content_type="application/json")
    assert first.status_code == 200

    second = api.post("/api/v1/auth/refresh/", {"refresh": tokens['refresh']},
                      content_type="application/json")
    assert second.status_code == 200
    assert second.json()['refresh'] == first.json()['refresh']


@pytest.mark.django_db
def test_a_refresh_token_replayed_long_after_rotation_is_refused(api):
    """The whole point of rotation: a token that has already been exchanged
    must stop being worth stealing. It used to hand back the *current* pair
    for the token's full 14-day lifetime."""
    from rest_framework_simplejwt.tokens import RefreshToken

    user = _user("replay@example.com")
    tokens = issue_tokens(user)
    jti = RefreshToken(tokens['refresh'])['jti']

    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens['refresh']},
                    content_type="application/json").status_code == 200

    # Age the rotation record past the grace window.
    record = cache.get(f"jwt:rt:{jti}")
    record['rotated_at'] = time.time() - 3600
    cache.set(f"jwt:rt:{jti}", record, timeout=3600)

    resp = api.post("/api/v1/auth/refresh/", {"refresh": tokens['refresh']},
                    content_type="application/json")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.errTokenRevoked"


@pytest.mark.django_db
def test_a_stale_replay_does_not_log_the_user_out_everywhere(api):
    """The refusal must not go through verify_and_revoke_refresh_token, which
    reads the rotation record as a user_id mismatch and answers by revoking
    every session the user has — the over-revocation this codebase has been
    bitten by before."""
    from rest_framework_simplejwt.tokens import RefreshToken

    user = _user("othertab@example.com")
    phone = issue_tokens(user)
    laptop = issue_tokens(user)
    jti = RefreshToken(phone['refresh'])['jti']

    api.post("/api/v1/auth/refresh/", {"refresh": phone['refresh']}, content_type="application/json")
    record = cache.get(f"jwt:rt:{jti}")
    record['rotated_at'] = time.time() - 3600
    cache.set(f"jwt:rt:{jti}", record, timeout=3600)
    api.post("/api/v1/auth/refresh/", {"refresh": phone['refresh']}, content_type="application/json")

    still_good = api.post("/api/v1/auth/refresh/", {"refresh": laptop['refresh']},
                          content_type="application/json")
    assert still_good.status_code == 200


# ---------------------------------------------------------------------
# A disabled superuser is disabled
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_a_disabled_superuser_cannot_sign_in(api):
    admin = _user("root@example.com", is_staff=True, is_superuser=True)
    admin.is_active = False
    admin.save(update_fields=['is_active'])

    resp = api.post(
        "/api/v1/auth/token/", {"email": "root@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.errAccountDisabled"


@pytest.mark.django_db
def test_a_disabled_superuser_gets_no_password_reset_mail(api):
    admin = _user("root2@example.com", is_staff=True, is_superuser=True)
    admin.is_active = False
    admin.save(update_fields=['is_active'])

    resp = api.post("/api/v1/auth/password/reset/request/", {"email": "root2@example.com"},
                    content_type="application/json")
    assert resp.status_code == 200  # generic answer, no account-status leak
    assert mail.outbox == []


# ---------------------------------------------------------------------
# The public profile page is no longer a walkable directory
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_public_profile_is_rate_limited(api):
    user = _user("public@example.com")

    for _ in range(30):
        assert api.get(f"/api/v1/auth/users/{user.id}/").status_code == 200
    assert api.get(f"/api/v1/auth/users/{user.id}/").status_code == 429


@pytest.mark.django_db
def test_a_suffix_saved_as_a_bare_string_is_not_read_letter_by_letter():
    """Region.edu_email_suffix is a list, but a single suffix saved as a
    plain string used to be iterated one character at a time, so
    `endswith('e')` made most of the internet look like a campus address."""
    from core.models import Region
    from core.region import edu_suffixes
    from accounts.views.auth import _is_valid_edu_email

    region = Region(code='XX', edu_email_suffix='.edu.tw')
    assert edu_suffixes(region) == ['.edu.tw']

    assert _is_valid_edu_email('someone@ntu.edu.tw') is True
    assert _is_valid_edu_email('someone@newcampus.example') is False


# ---------------------------------------------------------------------
# A presigned upload now carries a size limit R2 will enforce
# ---------------------------------------------------------------------

@pytest.fixture
def fake_r2(settings):
    # Presigning is local HMAC signing, so no credentials need to be real.
    settings.STORAGES = {**settings.STORAGES}
    settings.STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": "fake-access-key",
            "secret_key": "fake-secret-key",
            "bucket_name": "fake-bucket",
            "endpoint_url": "https://fake-account.r2.cloudflarestorage.com",
            "custom_domain": "media.example.invalid",
            "url_protocol": "https:",
            "addressing_style": "path",
            "signature_version": "s3v4",
            "region_name": "auto",
            "file_overwrite": False,
            "default_acl": None,
        },
    }
    return settings


@pytest.mark.django_db
def test_presigned_upload_signs_the_declared_size(api, fake_r2):
    user = _verified("uploader@example.com")
    resp = api.post(
        "/api/v1/listings/uploads/", {"content_type": "image/jpeg", "content_length": 2048},
        content_type="application/json", **bearer(user),
    )
    assert resp.status_code == 200
    # Signed, not merely echoed: R2 recomputes the signature over the header,
    # so a body of any other length is refused at the edge.
    assert "content-length" in resp.json()["upload_url"].lower()


@pytest.mark.django_db
@pytest.mark.parametrize("declared", [None, 0, -1, 5 * 1024 * 1024 + 1, "big", True])
def test_presigned_upload_refuses_an_unusable_size(api, fake_r2, declared):
    """Without this the 5MB limit lived only on the dev-only proxy path: a
    presigned PUT took a file of any size, a hundred times an hour."""
    user = _verified(f"uploader-{declared}@example.com")
    payload = {"content_type": "image/jpeg"}
    if declared is not None:
        payload["content_length"] = declared

    resp = api.post(
        "/api/v1/listings/uploads/", payload,
        content_type="application/json", **bearer(user),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errFileTooLarge"


# ---------------------------------------------------------------------
# IsRegionManager actually checks the region it is named after
# ---------------------------------------------------------------------

@pytest.mark.django_db
def test_region_manager_permission_refuses_another_region(api):
    """It returned True from has_object_permission, so every guarantee rested
    on each view remembering to filter its own queryset."""
    from adminapi.permissions import IsRegionManager
    from core.models import Region

    manager = _user("tw-manager@example.com", is_staff=True)
    manager.managed_regions.add(Region.objects.get(code='TW'))
    book = Book.objects.create(region_id='HK', isbn13="9789999999902", title="HK", source="manual")
    seller = _user("hk-seller@example.com")
    hk_listing = Listing.objects.create(
        region_id='HK', currency_id='HKD', book=book, seller=seller, price=100, condition="new",
    )
    tw_book = Book.objects.create(region_id='TW', isbn13="9789999999903", title="TW", source="manual")
    tw_listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', book=tw_book, seller=seller, price=100, condition="new",
    )

    request = type('R', (), {'user': manager})()
    permission = IsRegionManager()
    assert permission.has_object_permission(request, None, tw_listing) is True
    assert permission.has_object_permission(request, None, hk_listing) is False


@pytest.mark.django_db
def test_region_manager_permission_lets_a_superuser_through(api):
    from adminapi.permissions import IsRegionManager

    root = _user("root3@example.com", is_staff=True, is_superuser=True)
    seller = _user("hk-seller2@example.com")
    book = Book.objects.create(region_id='HK', isbn13="9789999999904", title="HK", source="manual")
    listing = Listing.objects.create(
        region_id='HK', currency_id='HKD', book=book, seller=seller, price=100, condition="new",
    )

    request = type('R', (), {'user': root})()
    assert IsRegionManager().has_object_permission(request, None, listing) is True

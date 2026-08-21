"""API tests for adminapi: staff-only Users/Listings/Orders admin endpoints
backing the Angular admin UI (phase 1)."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.models import School
from accounts.services import issue_tokens
from catalog.models import Book
from core.models import AuditEvent
from listings.models import Listing
from orders.models import Order

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture
def api():
    return Client()


def _auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def school(db):
    return School.objects.create(email_domain="admin-test.edu.tw", name="Admin Test University")


@pytest.fixture
def staff(db):
    u = User.objects.create_user(
        email="adm-staff@example.com", first_name="St", last_name="Aff", password=PASSWORD, is_staff=True
    )
    u.verified_at = timezone.now()
    u.save(update_fields=["verified_at"])
    return u


@pytest.fixture
def normal_user(db, school):
    u = User.objects.create_user(
        email="adm-normal@example.com", first_name="No", last_name="Rmal", password=PASSWORD
    )
    u.school = school
    u.verified_at = timezone.now()
    u.save(update_fields=["school", "verified_at"])
    return u


@pytest.fixture
def seller(db):
    u = User.objects.create_user(email="adm-seller@example.com", first_name="Se", last_name="Ller", password=PASSWORD)
    u.verified_at = timezone.now()
    u.save(update_fields=["verified_at"])
    return u


@pytest.fixture
def staff_header(staff):
    return _auth_header(staff)


@pytest.fixture
def normal_header(normal_user):
    return _auth_header(normal_user)


@pytest.fixture
def listing(db, seller):
    book = Book.objects.create(title="Admin Test Book", source="manual")
    return Listing.objects.create(book=book, seller=seller, price=100, condition="new")


@pytest.fixture
def order(db, normal_user, seller, listing):
    return Order.objects.create(
        buyer=normal_user, seller=seller, listing=listing, total_amount=100, status="pending"
    )


# ---------------------------------------------------------------------
# 403 for non-staff on every endpoint
# ---------------------------------------------------------------------


def test_non_staff_forbidden_users_list(api, normal_header):
    resp = api.get("/api/v1/admin/users/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_users_detail(api, normal_header, normal_user):
    resp = api.get(f"/api/v1/admin/users/{normal_user.id}/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_users_patch(api, normal_header, normal_user):
    resp = api.patch(
        f"/api/v1/admin/users/{normal_user.id}/",
        {"is_active": False},
        content_type="application/json",
        **normal_header,
    )
    assert resp.status_code == 403


def test_non_staff_forbidden_listings_list(api, normal_header):
    resp = api.get("/api/v1/admin/listings/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_listings_detail(api, normal_header, listing):
    resp = api.get(f"/api/v1/admin/listings/{listing.id}/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_listings_patch(api, normal_header, listing):
    resp = api.patch(
        f"/api/v1/admin/listings/{listing.id}/",
        {"status": "removed"},
        content_type="application/json",
        **normal_header,
    )
    assert resp.status_code == 403


def test_non_staff_forbidden_orders_list(api, normal_header):
    resp = api.get("/api/v1/admin/orders/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_orders_detail(api, normal_header, order):
    resp = api.get(f"/api/v1/admin/orders/{order.id}/", **normal_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_orders_force_cancel(api, normal_header, order):
    resp = api.post(
        f"/api/v1/admin/orders/{order.id}/force_cancel/",
        {"reason": "abuse report"},
        content_type="application/json",
        **normal_header,
    )
    assert resp.status_code == 403


def test_unauthenticated_forbidden(api, listing):
    resp = api.get("/api/v1/admin/users/")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------
# Users: list/detail/patch
# ---------------------------------------------------------------------


def test_users_list_includes_is_staff_field(api, staff_header, normal_user):
    resp = api.get("/api/v1/admin/users/", **staff_header)
    assert resp.status_code == 200
    results = resp.json()["results"]
    match = [r for r in results if r["id"] == normal_user.id][0]
    assert match["is_staff"] is False
    assert match["is_verified"] is True
    assert "email" in match and "display_name" in match


def test_users_list_search_by_email(api, staff_header, normal_user, staff):
    resp = api.get("/api/v1/admin/users/?q=adm-normal", **staff_header)
    assert resp.status_code == 200
    results = resp.json()["results"]
    ids = [r["id"] for r in results]
    assert normal_user.id in ids
    assert staff.id not in ids


def test_users_list_filter_is_active(api, staff_header, normal_user):
    normal_user.is_active = False
    normal_user.save(update_fields=["is_active"])
    resp = api.get("/api/v1/admin/users/?is_active=false", **staff_header)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["results"]]
    assert normal_user.id in ids

    resp2 = api.get("/api/v1/admin/users/?is_active=true", **staff_header)
    ids2 = [r["id"] for r in resp2.json()["results"]]
    assert normal_user.id not in ids2


def test_users_list_filter_school(api, staff_header, normal_user, school):
    resp = api.get(f"/api/v1/admin/users/?school={school.id}", **staff_header)
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["results"]]
    assert normal_user.id in ids


def test_users_patch_is_active_and_school(api, staff_header, staff, normal_user, school):
    resp = api.patch(
        f"/api/v1/admin/users/{normal_user.id}/",
        {"is_active": False, "school": None},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    normal_user.refresh_from_db()
    assert normal_user.is_active is False
    assert normal_user.school_id is None

    event = AuditEvent.objects.filter(kind="admin.user_updated").order_by("-id").first()
    assert event is not None
    assert event.user_id == staff.id
    assert event.meta["target_user_id"] == normal_user.id
    assert event.meta["changes"]["is_active"] is False


def test_users_patch_verified_toggle(api, staff_header, normal_user):
    normal_user.verified_at = None
    normal_user.save(update_fields=["verified_at"])

    resp = api.patch(
        f"/api/v1/admin/users/{normal_user.id}/",
        {"verified": True},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    normal_user.refresh_from_db()
    assert normal_user.verified_at is not None

    resp2 = api.patch(
        f"/api/v1/admin/users/{normal_user.id}/",
        {"verified": False},
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    normal_user.refresh_from_db()
    assert normal_user.verified_at is None


@pytest.mark.parametrize("field,value", [
    ("is_staff", True),
    ("is_superuser", True),
    ("password", "hacked-password"),
    ("groups", []),
    ("user_permissions", []),
])
def test_users_patch_rejects_forbidden_fields(api, staff_header, normal_user, field, value):
    resp = api.patch(
        f"/api/v1/admin/users/{normal_user.id}/",
        {field: value},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "admin.errForbiddenField"
    normal_user.refresh_from_db()
    assert normal_user.is_staff is False
    assert normal_user.is_superuser is False


# ---------------------------------------------------------------------
# Listings: list/detail/patch
# ---------------------------------------------------------------------


def test_listings_list_and_search(api, staff_header, listing):
    resp = api.get("/api/v1/admin/listings/?q=Admin+Test+Book", **staff_header)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["book"]["title"] == "Admin Test Book"
    assert results[0]["seller"]["email"] == listing.seller.email


def test_listings_patch_status_creates_audit_event(api, staff_header, staff, listing):
    resp = api.patch(
        f"/api/v1/admin/listings/{listing.id}/",
        {"status": "removed"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    listing.refresh_from_db()
    assert listing.status == "removed"

    event = AuditEvent.objects.filter(kind="admin.listing_status_changed").order_by("-id").first()
    assert event is not None
    assert event.meta["listing_id"] == str(listing.id)
    assert event.meta["old_status"] == "active"
    assert event.meta["new_status"] == "removed"


def test_listings_patch_invalid_status_rejected(api, staff_header, listing):
    resp = api.patch(
        f"/api/v1/admin/listings/{listing.id}/",
        {"status": "not-a-real-status"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400


def test_listings_patch_other_field_rejected(api, staff_header, listing):
    resp = api.patch(
        f"/api/v1/admin/listings/{listing.id}/",
        {"price": 5},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "admin.errForbiddenField"


# ---------------------------------------------------------------------
# Orders: list/detail (read-only) + force_cancel
# ---------------------------------------------------------------------


def test_orders_list_search(api, staff_header, order):
    resp = api.get(f"/api/v1/admin/orders/?q={order.buyer.email}", **staff_header)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["id"] == str(order.id)


def test_orders_no_patch_allowed(api, staff_header, order):
    resp = api.patch(
        f"/api/v1/admin/orders/{order.id}/",
        {"status": "cancelled"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code in (403, 405)


def test_orders_force_cancel_happy_path(api, staff_header, staff, order):
    resp = api.post(
        f"/api/v1/admin/orders/{order.id}/force_cancel/",
        {"reason": "fraud suspected"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    order.refresh_from_db()
    assert order.status == "cancelled"
    assert order.cancel_reason == "admin_override: fraud suspected"

    event = AuditEvent.objects.filter(kind="admin.order_force_cancelled").order_by("-id").first()
    assert event is not None
    assert event.meta["order_id"] == str(order.id)
    assert event.meta["reason"] == "fraud suspected"


def test_orders_force_cancel_already_final_rejected(api, staff_header, order):
    order.status = "completed"
    order.save(update_fields=["status"])

    resp = api.post(
        f"/api/v1/admin/orders/{order.id}/force_cancel/",
        {"reason": "too late"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "admin.errOrderAlreadyFinal"


def test_orders_force_cancel_short_reason_rejected(api, staff_header, order):
    resp = api.post(
        f"/api/v1/admin/orders/{order.id}/force_cancel/",
        {"reason": "x"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# Schools: delete
# ---------------------------------------------------------------------


def test_school_delete_prevented_if_users_exist(api, staff_header, school, normal_user):
    resp = api.delete(f"/api/v1/admin/schools/{school.id}/", **staff_header)
    assert resp.status_code == 400
    assert School.objects.filter(id=school.id).exists()


def test_school_delete_allowed_if_no_users(api, staff_header, school):
    resp = api.delete(f"/api/v1/admin/schools/{school.id}/", **staff_header)
    assert resp.status_code == 204
    assert not School.objects.filter(id=school.id).exists()


# ---------------------------------------------------------------------
# Moderation ReportActionView AuditEvent addition
# ---------------------------------------------------------------------


def test_report_action_creates_audit_event(api, staff_header, staff, listing):
    from moderation.models import Report

    report = Report.objects.create(reporter=listing.seller, listing=listing, reason="fake")
    # reporter is the seller here only to reuse the listing fixture; report
    # ownership doesn't matter for this audit-event assertion.
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/",
        {"status": "dismissed"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200

    event = AuditEvent.objects.filter(kind="admin.report_actioned").order_by("-id").first()
    assert event is not None
    assert event.user_id == staff.id
    assert event.meta["report_id"] == str(report.id)
    assert event.meta["action"] == "dismissed"
    assert event.meta["listing_id"] is None

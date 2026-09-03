"""API tests for the moderation Report workflow: create/list/self-lookup/staff
review endpoints, duplicate & self-report rejection, and the actioned->takedown
side effect."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from moderation.models import Report

User = get_user_model()
PASSWORD = "test-only-password-123"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def seller(db):
    u = User.objects.create_user(email="mod-seller@example.com", first_name="Se", last_name="Ller", password=PASSWORD)
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


@pytest.fixture
def reporter(db):
    u = User.objects.create_user(email="mod-reporter@example.com", first_name="Re", last_name="Porter", password=PASSWORD)
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


@pytest.fixture
def other_reporter(db):
    u = User.objects.create_user(email="mod-other@example.com", first_name="Ot", last_name="Her", password=PASSWORD)
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


@pytest.fixture
def staff(db):
    u = User.objects.create_user(
        email="mod-staff@example.com", first_name="St", last_name="Aff", password=PASSWORD, is_staff=True
    )
    # Staff moderation views are scoped to the regions a manager holds, the
    # same way adminapi already is — a bare is_staff account sees nothing.
    u.managed_regions.add('TW')
    from accounts.models import RegionVerification
    RegionVerification.objects.update_or_create(user=u, region_id='TW', defaults={'school': getattr(u, 'school', None), 'edu_email': u.email, 'verified_at': timezone.now()})
    return u


def _auth_header(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def reporter_header(reporter):
    return _auth_header(reporter)


@pytest.fixture
def other_reporter_header(other_reporter):
    return _auth_header(other_reporter)


@pytest.fixture
def seller_header(seller):
    return _auth_header(seller)


@pytest.fixture
def staff_header(staff):
    return _auth_header(staff)


@pytest.fixture
def listing(db, seller):
    book = Book.objects.create(region_id='TW', title="Moderation Test Book", source="manual")
    return Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new")


def _create_report(api, listing, header, reason="scam", detail=""):
    return api.post(
        "/api/v1/moderation/",
        {"listing": str(listing.id), "reason": reason, "detail": detail},
        content_type="application/json",
        **header,
    )


# ---------------------------------------------------------------------
# Report creation
# ---------------------------------------------------------------------


def test_create_report_success(api, listing, reporter_header):
    resp = _create_report(api, listing, reporter_header, detail="looks like a scam")
    assert resp.status_code == 201
    assert Report.objects.filter(listing=listing, reason="scam").exists()


def test_duplicate_open_report_rejected(api, listing, reporter_header):
    _create_report(api, listing, reporter_header)
    resp = _create_report(api, listing, reporter_header)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "moderation.errAlreadyReported"


def test_self_report_rejected(api, listing, seller_header):
    resp = _create_report(api, listing, seller_header)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "moderation.errCannotReportOwnListing"


# ---------------------------------------------------------------------
# My reports
# ---------------------------------------------------------------------


def test_my_reports_only_shows_own(api, listing, reporter, reporter_header, other_reporter, other_reporter_header):
    Report.objects.create(reporter=reporter, listing=listing, reason="fake")
    Report.objects.create(reporter=other_reporter, listing=listing, reason="scam")

    resp = api.get("/api/v1/moderation/mine/", **reporter_header)
    assert resp.status_code == 200
    results = resp.json().get("results", resp.json())
    assert len(results) == 1
    assert results[0]["reason"] == "fake"


# ---------------------------------------------------------------------
# Staff-only permission checks
# ---------------------------------------------------------------------


def test_non_staff_forbidden_from_list_all(api, reporter_header):
    resp = api.get("/api/v1/moderation/all/", **reporter_header)
    assert resp.status_code == 403


def test_non_staff_forbidden_from_action(api, listing, reporter, reporter_header):
    report = Report.objects.create(reporter=reporter, listing=listing, reason="fake")
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/",
        {"status": "actioned"},
        content_type="application/json",
        **reporter_header,
    )
    assert resp.status_code == 403


def test_staff_can_list_all_reports(api, listing, reporter, other_reporter, staff_header):
    Report.objects.create(reporter=reporter, listing=listing, reason="fake")
    Report.objects.create(reporter=other_reporter, listing=listing, reason="scam")

    resp = api.get("/api/v1/moderation/all/", **staff_header)
    assert resp.status_code == 200
    results = resp.json().get("results", resp.json())
    assert len(results) == 2


# ---------------------------------------------------------------------
# Staff actions
# ---------------------------------------------------------------------


def test_staff_action_actioned_takes_down_listing(api, listing, reporter, staff_header):
    report = Report.objects.create(reporter=reporter, listing=listing, reason="scam")
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/",
        {"status": "actioned"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    report.refresh_from_db()
    listing.refresh_from_db()
    assert report.status == "actioned"
    assert listing.status == "removed"


def test_staff_dismiss_leaves_listing_untouched(api, listing, reporter, staff_header):
    report = Report.objects.create(reporter=reporter, listing=listing, reason="other")
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/",
        {"status": "dismissed"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    report.refresh_from_db()
    listing.refresh_from_db()
    assert report.status == "dismissed"
    assert listing.status == "active"


def test_invalid_transition_on_resolved_report_rejected(api, listing, reporter, staff_header):
    report = Report.objects.create(reporter=reporter, listing=listing, reason="fake", status="actioned")
    resp = api.patch(
        f"/api/v1/moderation/{report.id}/",
        {"status": "dismissed"},
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 400

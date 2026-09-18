"""School requests: the "my school isn't supported" report and its admin queue."""
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts.models import SchoolRequest
from accounts.services import issue_tokens
from core.models import AuditEvent, Region

User = get_user_model()
PASSWORD = "test-only-password-123"
CREATE_URL = "/api/v1/auth/school-requests/"
ADMIN_LIST_URL = "/api/v1/admin/school-requests/"

VALID = {
    "school_name": "Example University",
    "school_website": "https://www.example.edu.tw/",
    "edu_email": "Student@Example.edu.tw",
}


def _auth(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _post(client, user, data, region="tw"):
    return client.post(f"{CREATE_URL}?region={region}", data, content_type="application/json", **_auth(user))


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="asker@example.com", password=PASSWORD, first_name="A", last_name="Sker")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="other@example.com", password=PASSWORD, first_name="O", last_name="Ther")


@pytest.fixture
def tw_admin(db):
    u = User.objects.create_user(email="tw-admin@example.com", password=PASSWORD, is_staff=True, first_name="T", last_name="W")
    u.managed_regions.add(Region.objects.get(code="TW"))
    return u


@pytest.fixture
def superuser(db):
    return User.objects.create_user(
        email="root@example.com", password=PASSWORD, is_staff=True, is_superuser=True, first_name="R", last_name="Oot",
    )


@pytest.fixture
def requests_in_both_regions(user, other_user):
    tw = SchoolRequest.objects.create(
        user=user, region_id="TW", school_name="TW College", school_website="https://tw.example.com",
    )
    hk = SchoolRequest.objects.create(
        user=other_user, region_id="HK", school_name="HK College", school_website="https://hk.example.com",
    )
    return tw, hk


# ---------------------------------------------------------------- create


def test_create_requires_login(api):
    resp = api.post(CREATE_URL, VALID, content_type="application/json")
    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "auth.errNotLoggedIn"}}
    assert not SchoolRequest.objects.exists()


@pytest.mark.parametrize("website", ["not a url", "example.com", "ftp://example.com/", "javascript:alert(1)", ""])
def test_create_rejects_bad_website(api, user, website):
    resp = _post(api, user, {**VALID, "school_website": website})
    assert resp.status_code == 400
    # An i18n key, so the frontend's parseApiError can render it.
    assert resp.json()["school_website"] == ["acct.errSchoolRequestWebsite"]
    assert not SchoolRequest.objects.exists()


@pytest.mark.parametrize("name", ["", "   ", "X", "N" * 256])
def test_create_rejects_bad_name(api, user, name):
    resp = _post(api, user, {**VALID, "school_name": name})
    assert resp.status_code == 400
    assert resp.json()["school_name"] == ["acct.errSchoolRequestName"]


def test_create_success(api, user):
    resp = _post(api, user, VALID)
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    assert body["school_name"] == "Example University"

    req = SchoolRequest.objects.get(pk=body["id"])
    assert req.user == user
    assert req.region_id == "TW"
    assert req.edu_email == "student@example.edu.tw"
    assert req.school_website == "https://www.example.edu.tw/"


def test_create_takes_region_from_request(api, user):
    resp = _post(api, user, VALID, region="hk")
    assert resp.status_code == 201
    assert SchoolRequest.objects.get().region_id == "HK"


def test_create_edu_email_is_optional(api, user):
    data = {k: v for k, v in VALID.items() if k != "edu_email"}
    resp = _post(api, user, data)
    assert resp.status_code == 201
    assert SchoolRequest.objects.get().edu_email == ""


def test_create_ignores_client_supplied_status_and_user(api, user, other_user):
    resp = _post(api, user, {**VALID, "status": "added", "user": other_user.id, "admin_note": "x"})
    assert resp.status_code == 201
    req = SchoolRequest.objects.get()
    assert req.status == "pending"
    assert req.user == user
    assert req.admin_note == ""


def test_duplicate_pending_returns_existing(api, user):
    first = _post(api, user, VALID)
    assert first.status_code == 201
    # Same school, different case and padding: still the same request.
    again = _post(api, user, {**VALID, "school_name": "  example UNIVERSITY "})
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert SchoolRequest.objects.count() == 1


def test_duplicate_that_races_past_the_check_hits_the_constraint(api, user):
    """Two identical submits can both miss the pending lookup; the partial
    unique constraint keeps it to one row and the loser gets that row."""
    from accounts.views.school_requests import SchoolRequestCreateView

    first = _post(api, user, VALID)
    real = SchoolRequestCreateView._pending
    calls = []

    def blind_first_time(*args):
        calls.append(args)
        return None if len(calls) == 1 else real(*args)

    with mock.patch.object(SchoolRequestCreateView, "_pending", staticmethod(blind_first_time)):
        again = _post(api, user, VALID)

    assert len(calls) == 2
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert SchoolRequest.objects.count() == 1


def test_duplicate_is_scoped_to_user_region_and_pending(api, user, other_user):
    first = _post(api, user, VALID)
    assert _post(api, other_user, VALID).status_code == 201
    assert _post(api, user, VALID, region="hk").status_code == 201

    # Once staff have ruled on it, asking again opens a new request.
    SchoolRequest.objects.filter(pk=first.json()["id"]).update(status="rejected")
    assert _post(api, user, VALID).status_code == 201
    assert SchoolRequest.objects.count() == 4


def test_create_is_throttled(api, user):
    for i in range(5):
        resp = _post(api, user, {**VALID, "school_name": f"School number {i}"})
        assert resp.status_code == 201
    resp = _post(api, user, {**VALID, "school_name": "One too many"})
    assert resp.status_code == 429


def test_account_deletion_drops_the_typed_address(user):
    req = SchoolRequest.objects.create(
        user=user, region_id="TW", school_name="Kept", school_website="https://k.example.com",
        edu_email="me@k.example.com",
    )
    user.delete()
    req.refresh_from_db()
    assert req.edu_email == ""
    assert req.school_name == "Kept"


# ---------------------------------------------------------------- admin list


def test_admin_list_requires_staff(api, user):
    assert api.get(ADMIN_LIST_URL).status_code == 401
    assert api.get(ADMIN_LIST_URL, **_auth(user)).status_code == 403


def test_region_admin_sees_only_own_region(api, tw_admin, requests_in_both_regions):
    tw, hk = requests_in_both_regions
    resp = api.get(ADMIN_LIST_URL, **_auth(tw_admin))
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["results"]]
    assert ids == [tw.id]

    # Asking for the other region explicitly does not widen the scope.
    resp = api.get(f"{ADMIN_LIST_URL}?region=hk", **_auth(tw_admin))
    assert resp.json()["results"] == []

    # Nor does going to the row directly.
    assert api.get(f"{ADMIN_LIST_URL}{hk.id}/", **_auth(tw_admin)).status_code == 404
    resp = api.patch(f"{ADMIN_LIST_URL}{hk.id}/", {"status": "added"}, content_type="application/json", **_auth(tw_admin))
    assert resp.status_code == 404
    hk.refresh_from_db()
    assert hk.status == "pending"


def test_superuser_sees_all_regions_and_can_filter(api, superuser, requests_in_both_regions):
    tw, hk = requests_in_both_regions
    resp = api.get(ADMIN_LIST_URL, **_auth(superuser))
    assert {r["id"] for r in resp.json()["results"]} == {tw.id, hk.id}

    resp = api.get(f"{ADMIN_LIST_URL}?region=hk", **_auth(superuser))
    assert [r["id"] for r in resp.json()["results"]] == [hk.id]


def test_admin_list_exposes_reporter(api, tw_admin, user, requests_in_both_regions):
    row = api.get(ADMIN_LIST_URL, **_auth(tw_admin)).json()["results"][0]
    assert row["user"] == {"id": user.id, "email": user.email}
    assert row["region"] == "TW"
    assert row["school_website"] == "https://tw.example.com"


def test_admin_list_status_filter_and_search(api, superuser, user, requests_in_both_regions):
    tw, hk = requests_in_both_regions
    SchoolRequest.objects.filter(pk=hk.pk).update(status="rejected")

    def ids(query):
        return {r["id"] for r in api.get(f"{ADMIN_LIST_URL}?{query}", **_auth(superuser)).json()["results"]}

    assert ids("status=pending") == {tw.id}
    assert ids("status=rejected") == {hk.id}
    assert ids("q=tw college") == {tw.id}             # name
    assert ids("q=hk.example") == {hk.id}             # website
    assert ids(f"q={user.email}") == {tw.id}          # reporter email


def test_admin_list_is_paginated(api, superuser, user):
    SchoolRequest.objects.bulk_create([
        SchoolRequest(user=user, region_id="TW", school_name=f"S{i}", school_website="https://s.example.com")
        for i in range(25)
    ])
    body = api.get(ADMIN_LIST_URL, **_auth(superuser)).json()
    assert body["count"] == 25
    assert len(body["results"]) == 20
    assert body["next"]


# ---------------------------------------------------------------- admin update


def test_admin_updates_status_and_note(api, tw_admin, requests_in_both_regions):
    tw, _hk = requests_in_both_regions
    url = f"{ADMIN_LIST_URL}{tw.id}/"
    resp = api.patch(url, {"status": "added", "admin_note": "Added as tw.example.com"}, content_type="application/json", **_auth(tw_admin))
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "added"
    assert resp.json()["admin_note"] == "Added as tw.example.com"

    tw.refresh_from_db()
    assert tw.status == "added"
    assert tw.admin_note == "Added as tw.example.com"
    event = AuditEvent.objects.get(kind="admin.school_request_updated")
    assert event.user == tw_admin
    assert event.meta["old_status"] == "pending"
    assert event.meta["new_status"] == "added"

    # Note alone leaves the status as it is.
    resp = api.patch(url, {"admin_note": ""}, content_type="application/json", **_auth(tw_admin))
    assert resp.status_code == 200
    tw.refresh_from_db()
    assert tw.status == "added"
    assert tw.admin_note == ""


@pytest.mark.parametrize("payload, code", [
    ({"status": "done"}, "admin.errInvalidStatus"),
    ({"school_name": "Renamed"}, "admin.errForbiddenField"),
    ({"status": "added", "user": 1}, "admin.errForbiddenField"),
    ({}, "admin.errInvalidField"),
    ({"admin_note": "x" * 2001}, "admin.errInvalidField"),
    ({"admin_note": 5}, "admin.errInvalidField"),
])
def test_admin_update_rejects_bad_payloads(api, tw_admin, requests_in_both_regions, payload, code):
    tw, _hk = requests_in_both_regions
    resp = api.patch(f"{ADMIN_LIST_URL}{tw.id}/", payload, content_type="application/json", **_auth(tw_admin))
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": code}}
    tw.refresh_from_db()
    assert tw.status == "pending"
    assert tw.school_name == "TW College"


def test_admin_update_requires_staff(api, user, requests_in_both_regions):
    tw, _hk = requests_in_both_regions
    resp = api.patch(f"{ADMIN_LIST_URL}{tw.id}/", {"status": "added"}, content_type="application/json", **_auth(user))
    assert resp.status_code == 403

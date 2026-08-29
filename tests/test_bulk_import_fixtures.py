"""Exercises the real admin bulk-import endpoints (adminapi.views.
AdminSchoolBulkImportView / AdminCategoryBulkImportView) against the actual
JSON fixtures authored for the School/Category admin import feature, living in
the sibling SchoolList/ and CategoryList/ projects rather than inside this repo.
"""

import json
import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.services import issue_tokens
from accounts.models import School
from core.models import Category

User = get_user_model()

def _find_fixture(subpath):
    candidates_tried = []
    
    env_dir = os.environ.get("CYCLEUNI_FIXTURES_DIR")
    if env_dir:
        candidate = Path(env_dir) / subpath
        candidates_tried.append(f"$CYCLEUNI_FIXTURES_DIR ({candidate})")
        if candidate.exists():
            return candidate, ""

    repo_root = Path(__file__).resolve().parent.parent.parent
    
    c1 = repo_root.parent / "CycleUniProject" / subpath
    candidates_tried.append(str(c1))
    if c1.exists():
        return c1, ""
        
    c2 = repo_root / subpath
    candidates_tried.append(str(c2))
    if c2.exists():
        return c2, ""

    reason = f"Fixture not present. Tried: " + " | ".join(candidates_tried) + ". You can set CYCLEUNI_FIXTURES_DIR env var to the fixture root."
    return c1, reason

SCHOOLS_FIXTURE, SCHOOLS_SKIP_REASON = _find_fixture("SchoolList/schools.TW.json")
CATEGORIES_FIXTURE, CATEGORIES_SKIP_REASON = _find_fixture("CategoryList/categories.json")


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def staff_header(db):
    user = User.objects.create_user(
        email="bulk-import-staff@example.com", first_name="Bulk", last_name="Staff",
        password="test-only-password-123", is_staff=True,
    )
    from accounts.models import RegionVerification
    from core.models import Region, Currency, Language
    twd, _ = Currency.objects.get_or_create(code="TWD", defaults={'symbol': "NT$", 'decimal_places': 0})
    lang, _ = Language.objects.get_or_create(code="zh-TW", defaults={'name': "Traditional Chinese"})
    tw, _ = Region.objects.get_or_create(code="TW", defaults={'name': "Taiwan", 'currency': twd, 'default_language': lang})
    user.managed_regions.add(tw)
    RegionVerification.objects.update_or_create(user=user, region_id='TW', defaults={'school': getattr(user, 'school', None), 'edu_email': user.email, 'verified_at': timezone.now()})
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


def _load(fixture_path):
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)["items"]


@pytest.mark.skipif(not SCHOOLS_FIXTURE.exists(), reason=SCHOOLS_SKIP_REASON)
def test_schools_fixture_preview_reports_all_as_new(api, staff_header, db):
    items = _load(SCHOOLS_FIXTURE)
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["new"]) == len(items)
    assert body["modified"] == []
    assert body["unchanged"] == []
    # Preview must not have written anything
    assert School.objects.count() == 0


@pytest.mark.skipif(not SCHOOLS_FIXTURE.exists(), reason=SCHOOLS_SKIP_REASON)
def test_schools_fixture_apply_creates_all_rows_and_is_idempotent(api, staff_header, db):
    items = _load(SCHOOLS_FIXTURE)

    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    assert School.objects.count() == len(items)

    ntu = School.objects.get(email_domain="ntu.edu.tw")
    assert ntu.name == "National Taiwan University"
    assert ntu.translations["zh-TW"]["name"] == "國立臺灣大學"

    # Re-running the exact same import must not create duplicates or errors
    resp2 = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["unchanged"]) == len(items)
    assert School.objects.count() == len(items)


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason=CATEGORIES_SKIP_REASON)
def test_categories_fixture_matches_seed_migration_for_overlapping_slugs(api, staff_header, db):
    """core/migrations/0002_seed_categories.py already seeds 8 of this fixture's
    12 slugs on a fresh database. The fixture intentionally mirrors that
    migration's exact wording, so importing it should report those 8 as
    'unchanged' and only the 4 genuinely-new slugs as 'new'."""
    items = _load(CATEGORIES_FIXTURE)
    seeded_slugs = {
        "management", "engineering", "science", "liberal-arts",
        "medicine", "eecs", "law", "social-sciences",
    }

    resp = api.post(
        "/api/v1/admin/categories/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    unchanged_slugs = {i["slug"] for i in body["unchanged"]}
    new_slugs = {i["slug"] for i in body["new"]}
    assert seeded_slugs <= unchanged_slugs
    assert new_slugs == {"agriculture", "arts-design", "education", "dentistry"}
    assert body["modified"] == []
    # Preview must not have written the new rows
    assert Category.objects.filter(slug="dentistry").exists() is False


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason=CATEGORIES_SKIP_REASON)
def test_categories_fixture_apply_creates_new_rows_and_is_idempotent(api, staff_header, db):
    items = _load(CATEGORIES_FIXTURE)

    resp = api.post(
        "/api/v1/admin/categories/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    assert Category.objects.count() == len(items)

    dentistry = Category.objects.get(slug="dentistry")
    assert dentistry.title == "牙醫學院"
    assert dentistry.translations["en"]["title"] == "College of Dentistry"

    eecs = Category.objects.get(slug="eecs")
    assert eecs.title == "電資學院"
    assert eecs.translations["en"]["title"] == "College of EECS"

    # Re-running the exact same import must not create duplicates or errors
    resp2 = api.post(
        "/api/v1/admin/categories/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["unchanged"]) == len(items)
    assert Category.objects.count() == len(items)


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason=CATEGORIES_SKIP_REASON)
def test_categories_fixture_detects_modification_against_seeded_row(api, staff_header, db):
    """If an admin has hand-edited a seeded category since, re-importing the
    canonical fixture must surface it as 'modified' (with a diff), not
    silently overwrite it or report it as unchanged."""
    stale = Category.objects.get(slug="engineering")
    stale.description = "手動編輯過的舊敘述"
    stale.save(update_fields=["description"])

    items = _load(CATEGORIES_FIXTURE)
    resp = api.post(
        "/api/v1/admin/categories/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    modified_slugs = {m["new"]["slug"] for m in body["modified"]}
    assert "engineering" in modified_slugs
    # Preview must not have mutated the existing row
    stale.refresh_from_db()
    assert stale.description == "手動編輯過的舊敘述"


# ---------------------------------------------------------------------
# home_static_{lang} cache invalidation: importing/editing schools or
# categories via the admin API must be visible on the homepage without
# waiting out HomeMetadataView's 24h cache.
# ---------------------------------------------------------------------


def _seed_home_static_cache():
    cache.set("home_static_TW_en", {"schools": [], "categories": []}, timeout=86400)
    cache.set("home_static_TW_zh-TW", {"schools": [], "categories": []}, timeout=86400)


@pytest.mark.skipif(not SCHOOLS_FIXTURE.exists(), reason=SCHOOLS_SKIP_REASON)
def test_schools_bulk_apply_invalidates_home_static_cache(api, staff_header, db):
    _seed_home_static_cache()
    items = _load(SCHOOLS_FIXTURE)
    api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert cache.get("home_static_TW_en") is None
    assert cache.get("home_static_zh-TW") is None


@pytest.mark.skipif(not CATEGORIES_FIXTURE.exists(), reason=CATEGORIES_SKIP_REASON)
def test_categories_bulk_apply_invalidates_home_static_cache(api, staff_header, db):
    _seed_home_static_cache()
    items = _load(CATEGORIES_FIXTURE)
    api.post(
        "/api/v1/admin/categories/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert cache.get("home_static_TW_en") is None
    assert cache.get("home_static_zh-TW") is None


def test_schools_bulk_preview_does_not_invalidate_cache(api, staff_header, db):
    _seed_home_static_cache()
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": [
            {"name": "Preview Only University", "email_domain": "preview-only.edu.tw", "translations": {}, "region": "TW"}
        ]}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    # A preview makes no DB changes, so the cache is still valid — must be left alone.
    assert cache.get("home_static_TW_en") is not None


def test_school_crud_invalidates_home_static_cache(api, staff_header, db):
    _seed_home_static_cache()
    resp = api.post(
        "/api/v1/admin/schools/",
        data=json.dumps({"name": "Direct Create University", "email_domain": "direct-create.edu.tw", "translations": {}, "region": "TW"}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 201
    assert cache.get("home_static_TW_en") is None

    school_id = resp.json()["id"]
    _seed_home_static_cache()
    resp2 = api.patch(
        f"/api/v1/admin/schools/{school_id}/",
        data=json.dumps({"name": "Renamed University"}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    assert cache.get("home_static_TW_en") is None

    _seed_home_static_cache()
    resp3 = api.delete(f"/api/v1/admin/schools/{school_id}/", **staff_header)
    assert resp3.status_code == 204
    assert cache.get("home_static_TW_en") is None


def test_category_crud_invalidates_home_static_cache(api, staff_header, db):
    _seed_home_static_cache()
    resp = api.post(
        "/api/v1/admin/categories/",
        data=json.dumps({"slug": "direct-create-cat", "title": "Direct Create", "description": "", "sort_order": 99, "is_active": True, "translations": {}, "region": "TW"}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 201
    assert cache.get("home_static_TW_en") is None

    cat_id = resp.json()["id"]
    _seed_home_static_cache()
    resp2 = api.patch(
        f"/api/v1/admin/categories/{cat_id}/",
        data=json.dumps({"title": "Renamed Category"}),
        content_type="application/json",
        **staff_header,
    )
    assert resp2.status_code == 200
    assert cache.get("home_static_TW_en") is None

    _seed_home_static_cache()
    resp3 = api.delete(f"/api/v1/admin/categories/{cat_id}/", **staff_header)
    assert resp3.status_code == 204
    assert cache.get("home_static_TW_en") is None


def test_schools_bulk_preview_with_out_of_scope_region(api, staff_header, db):
    items = [
        {"name": "TW Univ", "email_domain": "tw.edu.tw", "translations": {}, "region": "TW"},
        {"name": "HK Univ", "email_domain": "hk.edu.hk", "translations": {}, "region": "HK"}
    ]
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["new"]) == 1
    assert body["new"][0]["email_domain"] == "tw.edu.tw"
    assert len(body["forbidden"]) == 1
    assert body["forbidden"][0]["email_domain"] == "hk.edu.hk"
    assert School.objects.count() == 0

def test_schools_bulk_apply_with_out_of_scope_region(api, staff_header, db):
    items = [
        {"name": "TW Univ", "email_domain": "tw.edu.tw", "translations": {}, "region": "TW"},
        {"name": "HK Univ", "email_domain": "hk.edu.hk", "translations": {}, "region": "HK"}
    ]
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 200
    assert School.objects.filter(region_id="TW").count() == 1
    assert School.objects.filter(region_id="HK").count() == 0

def test_schools_bulk_superuser_cannot_import_cross_region(api, db):
    user = User.objects.create_superuser(
        email="super@example.com", first_name="Super", last_name="User",
        password="test-only-password-123"
    )
    superuser_header = {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}
    items = [
        {"name": "TW Univ", "email_domain": "tw.edu.tw", "translations": {}, "region": "TW"},
        {"name": "HK Univ", "email_domain": "hk.edu.hk", "translations": {}, "region": "HK"}
    ]
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "apply", "items": items}),
        content_type="application/json",
        **superuser_header,
    )
    assert resp.status_code == 200
    assert School.objects.filter(region_id="TW").count() == 1
    assert School.objects.filter(region_id="HK").count() == 0
    assert len(resp.json()["forbidden"]) == 1

def test_schools_bulk_all_out_of_scope_returns_403(api, staff_header, db):
    items = [
        {"name": "HK Univ 1", "email_domain": "hk1.edu.hk", "translations": {}, "region": "HK"},
        {"name": "HK Univ 2", "email_domain": "hk2.edu.hk", "translations": {}, "region": "HK"}
    ]
    resp = api.post(
        "/api/v1/admin/schools/bulk/?region=TW",
        data=json.dumps({"action": "preview", "items": items}),
        content_type="application/json",
        **staff_header,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin.errAllRegionsForbidden"

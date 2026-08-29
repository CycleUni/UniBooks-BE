"""API tests for listings, subscriptions, messaging, and home metadata."""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from accounts.models import School
from accounts.services import issue_tokens
from catalog.models import Book
from listings.models import Listing
from messaging.models import Conversation
from subscriptions.models import Subscription

User = get_user_model()

PASSWORD = "test-only-password-123"


def make_user(email, verified=True, school=None):
    from accounts.models import RegionVerification
    user = User.objects.create_user(email=email, first_name=email.split("@")[0], last_name="Test", password=PASSWORD)
    if verified:
        RegionVerification.objects.create(user=user, region_id='TW', school=school, edu_email=email, verified_at=timezone.now())
    return user


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_tokens(user)['access']}"}


@pytest.fixture
def api():
    return Client()


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def school(db):
    return School.objects.create(region_id='TW', name="Test University", email_domain="test.edu.tw")


@pytest.fixture
def seller(db, school):
    return make_user("seller@test.edu.tw", school=school)


@pytest.fixture
def buyer(db, school):
    return make_user("buyer@test.edu.tw", school=school)


@pytest.fixture
def book(db):
    return Book.objects.create(region_id='TW', isbn13="9785555555555", title="Listing Book", source="manual")


@pytest.fixture
def listing(db, seller, book, school):
    return Listing.objects.create(
        region_id='TW', currency_id='TWD', 
        book=book, seller=seller, school=school, price=200, condition="new", status="active"
    )


# ---------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------


def test_listing_list_filters_by_school_and_status(api, listing, seller, book):
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=90, condition="noted", status="sold")
    resp = api.get("/api/v1/listings/")
    assert resp.status_code == 200
    assert [item["price"] for item in resp.json()["results"]] == [200]

    resp = api.get("/api/v1/listings/?school=Test University")
    assert len(resp.json()["results"]) == 1
    resp = api.get("/api/v1/listings/?school=Other University")
    assert resp.json()["results"] == []


def test_listing_create_requires_login_and_verification(api, db, book, school):
    resp = api.post("/api/v1/listings/", {}, content_type="application/json")
    assert resp.status_code == 401
    # Assert the body, not just the status. Moving this view onto DRF's
    # IsAuthenticatedOrReadOnly kept the 401 but replaced the response with
    # DRF's own {"detail": ...}, which the frontend cannot localize — and a
    # status-only assertion stayed green through it. core.exception_handler
    # maps it back; this line is what keeps it mapped.
    assert resp.json() == {"error": {"code": "auth.errNotLoggedIn"}}

    unverified = make_user("unverified@test.edu.tw", verified=False)
    resp = api.post(
        "/api/v1/listings/",
        {"book": book.id, "price": 100, "condition": "new"},
        content_type="application/json",
        **bearer(unverified),
    )
    assert resp.status_code == 403


def test_listing_create_success_sets_seller_and_school(api, seller, book, school):
    resp = api.post(
        "/api/v1/listings/",
        {"book": book.id, "price": 250, "condition": "like_new"},
        content_type="application/json",
        **bearer(seller),
    )
    assert resp.status_code == 201
    created = Listing.objects.get(id=resp.json()["id"])
    assert created.seller == seller
    # This comment used to say the opposite of the assertion below it: that
    # `Listing.school` is left unset because `seller.school` is the source of
    # truth for school-scoped filtering. That was true before multi-region,
    # which moved school onto RegionVerification and switched the filter to
    # the listing's own FK. The stamp is now load-bearing — an unstamped
    # listing is invisible to every school-filtered query.
    assert created.school == school
    


def test_listing_create_validation_error(api, seller, book):
    resp = api.post(
        "/api/v1/listings/",
        {"book": book.id, "condition": "new"},
        content_type="application/json",
        **bearer(seller),
    )
    assert resp.status_code == 400


def test_listing_patch_and_delete_own_only(api, listing, seller, buyer):
    other_header = bearer(buyer)
    own_header = bearer(seller)

    # Others get 404 for both patch and delete
    assert (
        api.patch(
            f"/api/v1/listings/{listing.id}/",
            {"price": 1},
            content_type="application/json",
            **other_header,
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/listings/{listing.id}/", **other_header).status_code == 404

    # Owner can update
    resp = api.patch(
        f"/api/v1/listings/{listing.id}/",
        {"price": 180, "status": "sold"},
        content_type="application/json",
        **own_header,
    )
    assert resp.status_code == 200
    listing.refresh_from_db()
    assert listing.price == 180
    assert listing.status == "sold"

    # Invalid payload is rejected
    assert (
        api.patch(
            f"/api/v1/listings/{listing.id}/",
            {"price": -1},
            content_type="application/json",
            **own_header,
        ).status_code
        == 400
    )

    # Owner can delete
    assert api.delete(f"/api/v1/listings/{listing.id}/", **own_header).status_code == 204
    assert not Listing.objects.filter(id=listing.id).exists()


def test_listing_delete_cleans_up_r2_photos(api, seller, book):
    """When a listing with photo URLs is deleted, the Listing.delete()
    override attempts to remove each photo from object storage via
    default_storage.delete() — failures are swallowed so the DB row
    is always removed."""
    listing = Listing.objects.create(
        region_id='TW', currency_id='TWD', 
        book=book, seller=seller, price=200, condition="new", status="active",
        photos=[
            "https://media.example.invalid/listings/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.png",
            "https://media.example.invalid/listings/11111111-2222-3333-4444-555555555555.jpg",
        ],
    )
    listing_id = listing.id

    # Delete through the override
    listing.delete()
    assert not Listing.objects.filter(id=listing_id).exists()

    # Repeat with empty photos — should also succeed
    listing2 = Listing.objects.create(
        region_id='TW', currency_id='TWD', 
        book=book, seller=seller, price=20, condition="new", status="active",
        photos=[],
    )
    listing2_id = listing2.id
    listing2.delete()
    assert not Listing.objects.filter(id=listing2_id).exists()


def _real_png_bytes():
    """A genuine 1x1 PNG — ListingUploadDirectView decodes uploads with
    Pillow to confirm they're real images, so arbitrary bytes don't pass."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return buf.getvalue()


def test_listing_upload_presign_falls_back_to_direct_mode_without_r2(api, seller):
    # Tests run without R2 credentials configured (see conftest.py), so this
    # must report the local-dev fallback rather than a presigned R2 POST.
    resp = api.post(
        "/api/v1/listings/uploads/", {"content_type": "image/png"},
        content_type="application/json", **bearer(seller),
    )
    assert resp.status_code == 200
    assert resp.json() == {"mode": "direct"}


def test_listing_upload_presign_rejects_unsupported_content_type(api, seller):
    resp = api.post(
        "/api/v1/listings/uploads/", {"content_type": "application/pdf"},
        content_type="application/json", **bearer(seller),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errUnsupportedFileType"


def test_listing_upload_direct_stub(api, seller):
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("test.png", _real_png_bytes(), content_type="image/png")
    resp = api.post("/api/v1/listings/uploads/direct/", {"file": f}, **bearer(seller))
    assert resp.status_code == 200
    assert "url" in resp.json()


def test_listing_upload_presign_returns_r2_put_url_when_configured(api, seller, settings):
    # Presigned-URL generation is pure local HMAC signing (no network call
    # to R2), so this can be verified without real credentials. R2 doesn't
    # support S3 POST policies (returns 501), so this is a presigned PUT.
    # Rebind STORAGES wholesale rather than mutating settings.STORAGES["default"]
    # in place: the `settings` fixture only rolls back attributes that were
    # *assigned*, so an in-place nested mutation would leak the fake S3 backend
    # into every later test in the session.
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
    resp = api.post(
        "/api/v1/listings/uploads/", {"content_type": "image/jpeg"},
        content_type="application/json", **bearer(seller),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "presigned_put"
    # Uploads land under a per-user tmp/ prefix: `tmp/` is what R2's
    # lifecycle rule reaps, and the uploader's id is what lets
    # ListingUploadDeleteView prove ownership of a not-yet-attached file.
    tmp_prefix = f"tmp/listings/{seller.id}/"
    assert data["upload_url"].startswith(
        f"https://fake-account.r2.cloudflarestorage.com/fake-bucket/{tmp_prefix}"
    )
    assert "X-Amz-Signature" in data["upload_url"]
    assert data["photo_url"].startswith(f"https://media.example.invalid/{tmp_prefix}")
    assert data["photo_url"].endswith(".jpg")


def test_listing_upload_direct_rejects_non_image_file(api, seller):
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("test.png", b"<script>alert(1)</script>", content_type="image/png")
    resp = api.post("/api/v1/listings/uploads/direct/", {"file": f}, **bearer(seller))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errUnsupportedFileType"


# ---------------------------------------------------------------------
# Upload deletion (ownership). Every path here is fail-closed: anything not
# provably the caller's own must be refused, and the object must survive.
# ---------------------------------------------------------------------


@pytest.fixture
def stored_file():
    """Writes real objects into the test storage and cleans them up after."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    created = []

    def _store(key):
        name = default_storage.save(key, ContentFile(b"fake-image-bytes"))
        created.append(name)
        return name

    yield _store

    for name in created:
        if default_storage.exists(name):
            default_storage.delete(name)


def _media_url(key):
    return f"http://testserver/media/{key}"


def test_upload_delete_rejects_foreign_host_even_when_file_is_unattached(api, buyer, seller, listing, stored_file):
    """The original exploit: the ownership lookup matched on the full URL
    string, so swapping only the hostname made it find no listing — which
    skipped the check entirely — while the key was still derived from the
    path alone. Any logged-in user could delete any listing photo."""
    from django.core.files.storage import default_storage

    key = stored_file("listings/victim-photo.jpg")
    listing.photos = [_media_url(key)]
    listing.save(update_fields=["photos"])

    resp = api.delete(
        f"/api/v1/listings/uploads/delete/?url=http://evil.example/media/{key}",
        **bearer(buyer),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "listing.errPhotoHostNotAllowed"
    assert default_storage.exists(key), "victim's photo must survive"


def test_upload_delete_rejects_another_users_tmp_upload(api, buyer, seller, stored_file):
    from django.core.files.storage import default_storage

    key = stored_file(f"tmp/listings/{seller.id}/pending.jpg")
    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={_media_url(key)}", **bearer(buyer))

    assert resp.status_code == 403
    assert default_storage.exists(key)


def test_upload_delete_allows_own_tmp_upload(api, seller, stored_file):
    from django.core.files.storage import default_storage

    key = stored_file(f"tmp/listings/{seller.id}/pending.jpg")
    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={_media_url(key)}", **bearer(seller))

    assert resp.status_code == 204
    assert not default_storage.exists(key)


def test_upload_delete_allows_photo_on_own_listing(api, seller, listing, stored_file):
    from django.core.files.storage import default_storage

    key = stored_file("listings/mine.jpg")
    listing.photos = [_media_url(key)]
    listing.save(update_fields=["photos"])

    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={_media_url(key)}", **bearer(seller))

    assert resp.status_code == 204
    assert not default_storage.exists(key)


def test_upload_delete_rejects_photo_on_another_users_listing(api, buyer, listing, stored_file):
    from django.core.files.storage import default_storage

    key = stored_file("listings/not-yours.jpg")
    listing.photos = [_media_url(key)]
    listing.save(update_fields=["photos"])

    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={_media_url(key)}", **bearer(buyer))

    assert resp.status_code == 403
    assert default_storage.exists(key)


def test_upload_delete_rejects_unattached_permanent_key(api, seller, stored_file):
    """A `listings/` object nobody references can't be attributed to anyone,
    so it must be refused rather than deleted by whoever asks first."""
    from django.core.files.storage import default_storage

    key = stored_file("listings/orphan.jpg")
    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={_media_url(key)}", **bearer(seller))

    assert resp.status_code == 403
    assert default_storage.exists(key)


@pytest.mark.parametrize(
    "url",
    [
        "http://testserver/media/listings/../../../etc/passwd",
        "http://testserver/media/listings/%2e%2e/%2e%2e/etc/passwd",
    ],
)
def test_upload_delete_rejects_path_traversal(api, seller, url):
    resp = api.delete(f"/api/v1/listings/uploads/delete/?url={url}", **bearer(seller))
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# tmp/ promotion on listing create
# ---------------------------------------------------------------------


def test_listing_create_promotes_own_tmp_photo(api, seller, book, stored_file):
    from django.core.files.storage import default_storage

    key = stored_file(f"tmp/listings/{seller.id}/new.jpg")
    resp = api.post(
        "/api/v1/listings/",
        {"book": book.id, "price": 120, "condition": "new", "photos": [_media_url(key)]},
        content_type="application/json",
        **bearer(seller),
    )

    assert resp.status_code == 201
    photos = resp.json()["photos"]
    # Must end up on a permanent key — R2's lifecycle rule deletes tmp/ after
    # 7 days, so a listing left pointing at tmp/ would break next week.
    assert photos[0] == _media_url("listings/new.jpg")
    assert default_storage.exists("listings/new.jpg")
    assert not default_storage.exists(key)
    default_storage.delete("listings/new.jpg")


def test_listing_create_rejects_another_users_tmp_photo(api, seller, buyer, book, stored_file):
    """Promotion does copy+delete, so adopting someone else's pending upload
    would also destroy it for them."""
    from django.core.files.storage import default_storage

    key = stored_file(f"tmp/listings/{seller.id}/pending.jpg")
    resp = api.post(
        "/api/v1/listings/",
        {"book": book.id, "price": 120, "condition": "new", "photos": [_media_url(key)]},
        content_type="application/json",
        **bearer(buyer),
    )

    assert resp.status_code == 400
    assert default_storage.exists(key), "the real uploader's file must survive"


# ---------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------


def test_subscription_create_list_delete_flow(api, buyer, book, listing):
    header = bearer(buyer)

    resp = api.post(
        "/api/v1/subscriptions/", {"book_id": book.isbn13}, content_type="application/json", **header
    )
    assert resp.status_code == 201
    sub_id = resp.json()["id"]

    # Duplicate subscribe returns the existing record with 200
    resp = api.post(
        "/api/v1/subscriptions/", {"book_id": book.isbn13}, content_type="application/json", **header
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == sub_id

    resp = api.get("/api/v1/subscriptions/", **header)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["bookTitle"] == "Listing Book"
    # The pre-existing listing predates the subscription, so it is not "new"
    assert body[0]["newListingsCount"] == 0

    assert api.delete(f"/api/v1/subscriptions/{sub_id}/", **header).status_code == 204
    assert api.delete(f"/api/v1/subscriptions/{sub_id}/", **header).status_code == 404


def test_subscription_counts_listings_created_after_subscribing(api, buyer, seller, book):
    header = bearer(buyer)
    api.post("/api/v1/subscriptions/", {"book_id": book.isbn13}, content_type="application/json", **header)
    Listing.objects.create(region_id='TW', currency_id='TWD', book=book, seller=seller, price=100, condition="new", status="active")

    resp = api.get("/api/v1/subscriptions/", **header)
    assert resp.json()[0]["newListingsCount"] == 1


def test_subscription_create_validation(api, buyer):
    header = bearer(buyer)
    assert (
        api.post("/api/v1/subscriptions/", {}, content_type="application/json", **header).status_code
        == 400
    )
    assert (
        api.post(
            "/api/v1/subscriptions/", {"book_id": 999999}, content_type="application/json", **header
        ).status_code
        == 404
    )
    # Non-numeric, non-ISBN book_id falls through to the raw id lookup and
    # must return a clean 404 instead of a raw ValueError-induced 500.
    resp = api.post(
        "/api/v1/subscriptions/", {"book_id": "not-a-valid-id"}, content_type="application/json", **header
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------


def test_conversation_create_rules(api, listing, seller, buyer, db):
    unverified = make_user("stranger@test.edu.tw", verified=False)
    assert (
        api.post(
            "/api/v1/messaging/conversations/",
            {"listing_id": listing.id},
            content_type="application/json",
            **bearer(unverified),
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/messaging/conversations/", {}, content_type="application/json", **bearer(buyer)
        ).status_code
        == 400
    )
    # A seller cannot message their own listing
    assert (
        api.post(
            "/api/v1/messaging/conversations/",
            {"listing_id": listing.id},
            content_type="application/json",
            **bearer(seller),
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/v1/messaging/conversations/",
            {"listing_id": 999999},
            content_type="application/json",
            **bearer(buyer),
        ).status_code
        == 404
    )

    resp = api.post(
        "/api/v1/messaging/conversations/",
        {"listing_id": listing.id},
        content_type="application/json",
        **bearer(buyer),
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    # Re-creating returns the same conversation with 200
    resp = api.post(
        "/api/v1/messaging/conversations/",
        {"listing_id": listing.id},
        content_type="application/json",
        **bearer(buyer),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == conv_id


def test_conversation_list_shows_other_party_and_latest_message(api, listing, seller, buyer):
    # Message bodies are no longer stored in Django (CFEdgeChat owns message
    # history); the CFEdgeChat webhook mirrors only the latest message body
    # onto the conversation row for the inbox preview (see
    # messaging.views.EdgeChatWebhookView), so tests set it directly here.
    conv = Conversation.objects.create(listing=listing, buyer=buyer, latest_message_body="latest reply")

    for viewer, other_party in ((buyer, seller.display_name), (seller, buyer.display_name)):
        resp = api.get("/api/v1/messaging/conversations/", **bearer(viewer))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["other_party"] == other_party
        assert body["results"][0]["latest_message"] == "latest reply"
        assert body["results"][0]["listing_title"] == "Listing Book"


# NOTE: per-conversation message history (list/send) is no longer exposed by
# Django — it moved to CFEdgeChat (see messaging/views.py's ChatTokenView and
# EdgeChatWebhookView docstrings). The equivalent access-control coverage
# (non-participant rejection, malformed/unknown conversation id handling) now
# lives in tests/test_api_edge_chat.py against the chat-token issuance flow
# that CFEdgeChat itself relies on for authorization.


# ---------------------------------------------------------------------
# Home metadata
# ---------------------------------------------------------------------


def test_home_metadata_defaults_to_region_default(api, school, buyer, book):
    Subscription.objects.create(region_id='TW', user=buyer, book=book)
    resp = api.get("/api/v1/core/metadata/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lang"] == "zh-TW"
    assert body["schools"][0]["name"] == "Test University"
    assert body["schools"][0]["display_name"] == "Test University"
    # Categories are real records seeded by migration, not hardcoded mock data
    assert [c["title"] for c in body["categories"]] == [
        "商管學院",
        "工學院",
        "理學院",
        "文學院",
        "醫學院",
        "電資學院",
        "法學院",
        "社科院",
    ]
    assert body["waitlist"][0] == {
        "title": "Listing Book",
        "count": 1,
        "book_id": book.id,
        "cover_url": book.cover_url,
    }


def test_home_metadata_localizes_to_zh_tw(api, school):
    school.translations = {"zh-TW": {"name": "測試大學"}}
    school.save(update_fields=["translations"])

    resp = api.get("/api/v1/core/metadata/?lang=zh-TW")
    body = resp.json()
    assert body["lang"] == "zh-TW"
    # Canonical name is kept for filtering; display_name is localized
    assert body["schools"][0]["name"] == "Test University"
    assert body["schools"][0]["display_name"] == "測試大學"
    # zh-TW has no dedicated translation entry (only "en" does), so it falls
    # back to the canonical fields, which are already Chinese
    assert body["categories"][0] == {"title": "商管學院", "desc": "經濟、會計、企管", "slug": "management"}


def test_home_metadata_accept_language_and_fallbacks(api, school):
    # Any Chinese variant maps to zh-TW
    resp = api.get("/api/v1/core/metadata/", HTTP_ACCEPT_LANGUAGE="zh-Hant-TW,zh;q=0.9")
    assert resp.json()["lang"] == "zh-TW"
    # A language without translations falls back to canonical fields.
    # School's canonical `name` is English (see the `school` fixture); Category's
    # canonical `title` is Chinese (see core/migrations/0002_seed_categories.py) —
    # each model's fallback reflects whatever it stores as canonical.
    resp = api.get("/api/v1/core/metadata/?lang=ja")
    body = resp.json()
    assert body["schools"][0]["display_name"] == "Test University"
    assert body["categories"][0]["title"] == "商管學院"

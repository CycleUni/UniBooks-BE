"""Shared object-storage upload helpers.

Lives in `core` (which depends on no other app) because both `listings` and
`messaging` upload user images and previously carried byte-identical copies
of every helper below.

Note on `tmp/` keys: unfinished uploads are written under a `tmp/` prefix and
reaped by an R2 Object Lifecycle rule (`tmp/` → delete after 7 days), not by
application code. That means anything left under `tmp/` *will* disappear —
promoting a tmp object to its permanent key must therefore never fail
silently (see listings.utils.promote_tmp_photos).
"""

import logging
from urllib.parse import unquote, urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

# Declared Content-Type → extension. The presigned-upload path can only check
# the client's declared type up front (the bytes never reach this server);
# the direct-proxy fallback additionally decodes them with Pillow.
ALLOWED_CONTENT_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif'}
# Pillow's own detected format → extension.
ALLOWED_IMAGE_FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def validated_content_length(data, max_bytes=MAX_UPLOAD_SIZE_BYTES):
    """The client's declared upload size, or None if it isn't usable.

    R2 rejects S3 POST policies, which is what would normally carry a
    `content-length-range` condition, so the size limit used to hold on the
    dev-only proxy path and nowhere else: a presigned PUT accepted a file of
    any size, and an authenticated account could run up storage costs a
    hundred times an hour. Signing `ContentLength` closes it — the signature
    covers the header, so R2 refuses anything whose actual length differs
    from the one declared here, and this is where the declared value is
    checked against the limit.
    """
    raw = data.get('content_length')
    if isinstance(raw, bool):
        return None
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None
    if size <= 0 or size > max_bytes:
        return None
    return size


def r2_client_and_options():
    """Returns (boto3 S3 client, OPTIONS dict) if STORAGES["default"] is the
    R2/S3 backend, else (None, None) — the local FileSystemStorage dev
    fallback has no S3-compatible endpoint to presign against."""
    default_storage_config = settings.STORAGES["default"]
    if default_storage_config["BACKEND"] != "storages.backends.s3.S3Storage":
        return None, None

    import boto3
    from botocore.config import Config

    options = default_storage_config["OPTIONS"]
    client = boto3.client(
        "s3",
        endpoint_url=options["endpoint_url"],
        aws_access_key_id=options["access_key"],
        aws_secret_access_key=options["secret_key"],
        region_name=options.get("region_name", "auto"),
        config=Config(signature_version=options.get("signature_version", "s3v4")),
    )
    return client, options


def allowed_storage_hosts(request=None):
    """Hosts an uploaded-file URL is allowed to point at: the configured R2
    custom domain in production, plus this server's own host in local dev
    (FileSystemStorage serves uploads same-origin under MEDIA_URL)."""
    hosts = set()
    storage = settings.STORAGES.get("default", {})
    custom_domain = storage.get("OPTIONS", {}).get("custom_domain")
    if custom_domain:
        hosts.add(custom_domain.lower())
    if request is not None:
        hosts.add(request.get_host().split(':')[0].lower())
    return hosts


def storage_key_from_url(url, request=None):
    """The object-storage key for a URL we ourselves issued, or None if the
    URL doesn't demonstrably point at our own storage.

    Returning None (rather than a best-effort key) matters: callers use this
    to decide what to delete, and deriving a key from an arbitrary host would
    let anyone name any object in our bucket by simply swapping the hostname
    while keeping the path.
    """
    if not isinstance(url, str) or not url:
        return None

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return None
    if parsed.hostname.lower() not in allowed_storage_hosts(request):
        return None

    # Decode before inspecting segments so percent-encoded traversal
    # (%2e%2e, %2f) can't slip past the checks below.
    path = unquote(parsed.path)

    media_url = getattr(settings, 'MEDIA_URL', '/media/') or '/'
    if media_url != '/' and path.startswith(media_url):
        key = path[len(media_url):]
    else:
        key = path.lstrip('/')

    if not key or '\\' in key or '..' in key.split('/'):
        return None
    return key


def detect_image_extension(file):
    """Extension for `file` based on Pillow's own decoding of the bytes, or
    None if it isn't a supported image.

    Decodes rather than trusting the declared Content-Type or filename —
    closes off disguised-file uploads (e.g. an HTML/SVG payload renamed to
    photo.jpg, which browsers may sniff and execute: stored XSS).
    Leaves the file rewound so the caller can save it. Size is deliberately
    *not* checked here so callers can report "too large" separately from
    "unsupported type".
    """
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(file)
        image.verify()
        ext = ALLOWED_IMAGE_FORMATS.get(image.format)
    except (UnidentifiedImageError, OSError):
        ext = None
    file.seek(0)
    return ext

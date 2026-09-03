import uuid

from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.files.storage import default_storage
from listings.models import Listing

from rest_framework.throttling import ScopedRateThrottle

# Extension is derived from this allowlist, never from the client-supplied
# filename or Content-Type header alone (both are trivially spoofable). The
# presigned-upload path (ListingUploadURLView) can only check the declared
# Content-Type and size up front — the file never touches this server — so
# R2's own signed `Content-Type` and `Content-Length` conditions are what
# enforce them at upload time. The direct-proxy fallback
# (ListingUploadDirectView, dev-only) additionally decodes the bytes with
# Pillow since it does receive them.
from core.uploads import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    detect_image_extension,
    r2_client_and_options,
    validated_content_length,
    storage_key_from_url,
)
from listings.utils import PERMANENT_LISTING_PREFIX, tmp_key_prefix


class ListingUploadURLView(views.APIView):
    """Issues a presigned R2 PUT URL so the browser uploads the file
    directly to object storage — it never passes through this server.

    R2 doesn't support S3 POST policies, which is what would normally carry
    a `content-length-range` condition, so `MAX_UPLOAD_SIZE_BYTES` used to
    hold on the dev-only direct path and nowhere else. The size is instead
    signed into the URL as `ContentLength`: the client declares it, this view
    checks it against the limit, and R2 refuses any body whose actual length
    differs from the signed one.

    Falls back to `mode: "direct"` (see ListingUploadDirectView) when R2
    isn't configured (local dev without real credentials — see
    core.conf.resolve_storage_config), since there's no S3-compatible
    endpoint to presign against in that case.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        content_type = request.data.get('content_type')
        ext = ALLOWED_CONTENT_TYPES.get(content_type)
        if not ext:
            return Response({"error": {"code": "listing.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        client, options = r2_client_and_options()
        if client is None:
            return Response({"mode": "direct"})

        # Only the signed path needs this: the declared size is what gets
        # signed into the URL, so R2 itself refuses a body of any other
        # length. The direct fallback below weighs the real bytes instead.
        content_length = validated_content_length(request.data)
        if content_length is None:
            return Response({"error": {"code": "listing.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        key = f"{tmp_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"

        # Cloudflare R2 does not support S3 POST policies (returns 501).
        # We must use PUT presigned URLs instead.
        upload_url = client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': options["bucket_name"],
                'Key': key,
                'ContentType': content_type,
                'ContentLength': content_length,
            },
            ExpiresIn=300,
        )

        protocol = options.get("url_protocol", "https:").rstrip(":")
        photo_url = f"{protocol}://{options['custom_domain']}/{key}"

        return Response({
            "mode": "presigned_put",
            "upload_url": upload_url,
            "photo_url": photo_url,
        })


class ListingUploadDirectView(views.APIView):
    """Local-dev-only fallback: proxies the file through this server into
    FileSystemStorage when R2 isn't configured. The frontend only calls this
    when ListingUploadURLView responded with `mode: "direct"`."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": {"code": "listing.errNoFile"}}, status=status.HTTP_400_BAD_REQUEST)

        if file.size > MAX_UPLOAD_SIZE_BYTES:
            return Response({"error": {"code": "listing.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        ext = detect_image_extension(file)
        if not ext:
            return Response({"error": {"code": "listing.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        filename = f"{tmp_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"
        path = default_storage.save(filename, file)
        url = request.build_absolute_uri(default_storage.url(path))

        return Response({"url": url})


class ListingUploadDeleteView(views.APIView):
    """Deletes an image the caller uploaded but hasn't attached to a listing
    yet, or is removing from a listing they own.

    Both paths are fail-closed — anything not provably the caller's is
    refused rather than deleted:

    - `tmp/listings/<user_id>/…` — only the user whose id is in the key.
      Nothing else records who uploaded an unattached file, which is exactly
      why the uploader's id is baked into the key at upload time.
    - `listings/…` — only if a listing the caller owns actually references
      this exact URL.

    The URL's host is validated too (storage_key_from_url): deriving the key
    from `path` alone would let anyone name any object in the bucket just by
    swapping the hostname while keeping the path.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def delete(self, request):
        url = request.query_params.get('url') or request.data.get('url')
        if not url or not isinstance(url, str):
            return Response({"error": {"code": "listing.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        key = storage_key_from_url(url, request)
        if key is None:
            return Response({"error": {"code": "listing.errPhotoHostNotAllowed"}}, status=status.HTTP_400_BAD_REQUEST)

        if key.startswith(tmp_key_prefix(request.user.id)):
            default_storage.delete(key)
            return Response(status=status.HTTP_204_NO_CONTENT)

        if key.startswith(f"{PERMANENT_LISTING_PREFIX}/"):
            # icontains only narrows the candidate rows (JSONField has no
            # cross-backend exact-membership lookup: `contains` is unsupported
            # on SQLite). Exact membership is then confirmed in Python, so a
            # substring collision can't authorise a delete.
            candidates = Listing.objects.filter(seller=request.user, photos__icontains=url)
            if any(url in (listing.photos or []) for listing in candidates):
                default_storage.delete(key)
                return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({"error": {"code": "listing.errPhotoNotOwned"}}, status=status.HTTP_403_FORBIDDEN)

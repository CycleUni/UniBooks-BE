import logging
import uuid

logger = logging.getLogger(__name__)

from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from django.core.files.storage import default_storage

from core.uploads import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    detect_image_extension,
    r2_client_and_options,
    validated_content_length,
    storage_key_from_url,
)
from ads.utils import tmp_ad_key_prefix

from ..permissions import IsRegionManager

class AdminAdUploadURLView(views.APIView):
    permission_classes = [IsAdminUser, IsRegionManager]

    def post(self, request):
        content_type = request.data.get('content_type')
        ext = ALLOWED_CONTENT_TYPES.get(content_type)
        if not ext:
            return Response({"error": {"code": "admin.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        client, options = r2_client_and_options()
        if client is None:
            return Response({"mode": "direct"})

        # Only the signed path needs this: the declared size is what gets
        # signed into the URL, so R2 itself refuses a body of any other
        # length. The direct fallback below weighs the real bytes instead.
        content_length = validated_content_length(request.data)
        if content_length is None:
            return Response({"error": {"code": "admin.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        key = f"{tmp_ad_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"

        try:
            url = client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': options["bucket_name"],
                    'Key': key,
                    'ContentType': content_type,
                    'ContentLength': content_length,
                },
                ExpiresIn=300
            )
        except Exception:
            logger.exception("Failed to generate presigned URL for ad upload")
            return Response({"error": {"code": "admin.errUploadPresignFailed"}}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        protocol = options.get("url_protocol", "https:").rstrip(":")
        return Response({
            "mode": "presigned_put",
            "upload_url": url,
            "photo_url": f"{protocol}://{options['custom_domain']}/{key}"
        })

class AdminAdUploadDirectView(views.APIView):
    permission_classes = [IsAdminUser, IsRegionManager]

    def post(self, request):
        if 'file' not in request.FILES:
            return Response({"error": {"code": "admin.errMissingFile"}}, status=status.HTTP_400_BAD_REQUEST)
        
        file_obj = request.FILES['file']
        
        if file_obj.size > MAX_UPLOAD_SIZE_BYTES:
            return Response({"error": {"code": "admin.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)
            
        ext = detect_image_extension(file_obj)
        if not ext:
            return Response({"error": {"code": "admin.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        key = f"{tmp_ad_key_prefix(request.user.id)}{uuid.uuid4().hex}.{ext}"
        
        saved_key = default_storage.save(key, file_obj)
        return Response({
            "mode": "direct",
            "photo_url": default_storage.url(saved_key)
        })

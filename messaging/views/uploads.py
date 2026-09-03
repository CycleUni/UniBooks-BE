import uuid

from django.core.exceptions import ValidationError
from rest_framework import views, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from messaging.models import Conversation

# Shared with listings/ rather than re-declared here — these were previously
# a byte-for-byte copy of the listing upload helpers.
from core.uploads import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    detect_image_extension,
    r2_client_and_options,
    validated_content_length,
)


class ChatUploadURLView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        conversation_id = request.data.get('conversation_id')
        if not conversation_id:
            return Response({"error": {"code": "msg.errConversationRequired"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        if conversation.buyer_id != request.user.id and conversation.listing.seller_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)

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
            return Response({"error": {"code": "msg.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        key = f"chat/{conversation_id}/{uuid.uuid4().hex}.{ext}"

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


class ChatUploadDirectView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'upload'

    def post(self, request):
        conversation_id = request.data.get('conversation_id')
        if not conversation_id:
            return Response({"error": {"code": "msg.errConversationRequired"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = Conversation.objects.select_related('listing').get(id=conversation_id)
        except (Conversation.DoesNotExist, ValidationError):
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        if conversation.buyer_id != request.user.id and conversation.listing.seller_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)

        from django.core.files.storage import default_storage

        file = request.FILES.get('file')
        if not file:
            return Response({"error": {"code": "listing.errNoFile"}}, status=status.HTTP_400_BAD_REQUEST)

        if file.size > MAX_UPLOAD_SIZE_BYTES:
            return Response({"error": {"code": "listing.errFileTooLarge"}}, status=status.HTTP_400_BAD_REQUEST)

        ext = detect_image_extension(file)
        if not ext:
            return Response({"error": {"code": "listing.errUnsupportedFileType"}}, status=status.HTTP_400_BAD_REQUEST)

        filename = f"chat/{conversation_id}/{uuid.uuid4().hex}.{ext}"
        path = default_storage.save(filename, file)
        url = request.build_absolute_uri(default_storage.url(path))

        return Response({"url": url})

from django.db import IntegrityError, transaction
from rest_framework import status, views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from core.throttling import ScopedThrottle

from accounts.models import SchoolRequest
from accounts.serializers import SchoolRequestCreateSerializer
from core.region import get_region


class SchoolRequestCreateView(views.APIView):
    """POST /api/v1/auth/school-requests/ — "my school isn't supported yet".

    Offered by the account page when verification answers
    acct.errSchoolNotSupported. The region is the one the user is browsing,
    the same one that verification was just attempted in.

    A repeat of a request that is still pending (same user, region and
    school name, case-insensitive) answers 200 with the existing row instead
    of creating a second one or failing. The user's intent — "tell them about
    my school" — has already been met, so an error would only make them try
    again; and it keeps a double-clicked submit from showing a failure right
    after the success. 201 means a new row, 200 means it was already there.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedThrottle]
    throttle_scope = 'school-request'

    def post(self, request):
        region = get_region(request)
        if region is None:
            return Response({"error": {"code": "sys.errUnknownRegion"}}, status=status.HTTP_400_BAD_REQUEST)

        serializer = SchoolRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['school_name']

        existing = self._pending(request.user, region, name)
        if existing:
            return Response(SchoolRequestCreateSerializer(existing).data, status=status.HTTP_200_OK)

        try:
            # Savepoint so the IntegrityError below leaves the request's
            # transaction usable for the lookup that follows it.
            with transaction.atomic():
                instance = serializer.save(user=request.user, region_id=region.pk)
        except IntegrityError:
            # Lost the race to a concurrent identical submit; the database
            # constraint kept it to one row, and that row is the answer.
            existing = self._pending(request.user, region, name)
            if existing is None:
                raise
            return Response(SchoolRequestCreateSerializer(existing).data, status=status.HTTP_200_OK)

        return Response(SchoolRequestCreateSerializer(instance).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _pending(user, region, name):
        return SchoolRequest.objects.filter(
            user=user,
            region_id=region.pk,
            school_name__iexact=name,
            status=SchoolRequest.STATUS_PENDING,
        ).first()

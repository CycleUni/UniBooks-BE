from rest_framework import serializers
from messaging.models import Conversation
from .models import Report, ChatReport


class ReportListingSerializer(serializers.Serializer):
    """Lightweight nested serializer, exposes only the listing fields needed by the report page."""
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(source='book.title', read_only=True)


class ReportReporterSerializer(serializers.Serializer):
    """Exposes only the minimal fields staff need to identify the reporter, to avoid leaking sensitive info."""
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)


class ReportCreateSerializer(serializers.ModelSerializer):
    """For creating a report. `reporter` is not writable by the client; the view sets it from request.user."""

    class Meta:
        model = Report
        fields = ('id', 'listing', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = ('id', 'status', 'created_at')

    def validate_reason(self, value):
        valid_reasons = dict(Report.REASON_CHOICES)
        if value not in valid_reasons:
            raise serializers.ValidationError({"code": "moderation.errInvalidReason"})
        return value


class ReportSerializer(serializers.ModelSerializer):
    """For reading reports (user's own submitted reports / staff review list)."""

    listing = ReportListingSerializer(read_only=True)
    reporter = ReportReporterSerializer(read_only=True)

    class Meta:
        model = Report
        fields = ('id', 'listing', 'reporter', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = fields


class ReportStatusUpdateSerializer(serializers.ModelSerializer):
    """Staff-only: only allows the valid transitions open -> actioned / open -> dismissed."""

    class Meta:
        model = Report
        fields = ('status',)

    def validate_status(self, value):
        valid_transitions = {
            'open': ['actioned', 'dismissed'],
            'actioned': [],
            'dismissed': [],
        }

        current_status = self.instance.status
        if value not in valid_transitions.get(current_status, []):
            raise serializers.ValidationError(f"Cannot transition from {current_status} to {value}.")

        return value


# ── ChatReport serializers ──────────────────────────────────────────────


class ChatReportConversationSerializer(serializers.Serializer):
    """Lightweight serializer for the conversation nested in a chat report."""
    id = serializers.UUIDField(read_only=True)
    listing_title = serializers.CharField(source='listing.book.title', read_only=True)


class ChatReportReporterSerializer(serializers.Serializer):
    """Exposes only the minimal fields staff need to identify the reporter."""
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)


class ChatReportReportedPartySerializer(serializers.Serializer):
    """Exposes only the minimal fields staff need to identify the reported party."""
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)


class ChatReportCreateSerializer(serializers.ModelSerializer):
    """For creating a chat report. `reporter` is not writable by the client; the view sets it from request.user."""

    class Meta:
        model = ChatReport
        fields = ('id', 'conversation', 'reported_party', 'reason', 'detail', 'flagged_message_ids', 'status', 'created_at')
        read_only_fields = ('id', 'status', 'created_at')

    def validate_conversation(self, value):
        reporter = self.context['request'].user
        # `value` is already the Conversation row; no need to re-query it twice.
        if reporter.id not in (value.buyer_id, value.listing.seller_id):
            raise serializers.ValidationError("You are not a participant in this conversation.")
        return value

    def validate(self, attrs):
        # The reported party is whoever the reporter is talking to, full stop.
        # Taking it from the client unvalidated let a report be filed against
        # any user id at all, attached to a conversation they were never in.
        conversation = attrs.get('conversation')
        reported_party = attrs.get('reported_party')
        if conversation is not None and reported_party is not None:
            reporter = self.context['request'].user
            other_party_id = (
                conversation.listing.seller_id if reporter.id == conversation.buyer_id else conversation.buyer_id
            )
            if reported_party.id != other_party_id:
                raise serializers.ValidationError({"reported_party": "moderation.errInvalidReportedParty"})
        return attrs

    def validate_reason(self, value):
        valid_reasons = dict(ChatReport.REASON_CHOICES)
        if value not in valid_reasons:
            raise serializers.ValidationError({"code": "moderation.errInvalidReason"})
        return value


class ChatReportSerializer(serializers.ModelSerializer):
    """For reading chat reports (user's own / staff list / staff detail)."""

    conversation = ChatReportConversationSerializer(read_only=True)
    reporter = ChatReportReporterSerializer(read_only=True)
    reported_party = ChatReportReportedPartySerializer(read_only=True)

    class Meta:
        model = ChatReport
        fields = ('id', 'conversation', 'reporter', 'reported_party', 'reason', 'detail', 'flagged_message_ids', 'status', 'created_at')
        read_only_fields = fields


class ChatReportStatusUpdateSerializer(serializers.ModelSerializer):
    """Staff only: allows open → actioned / open → dismissed. Terminal states are locked."""

    class Meta:
        model = ChatReport
        fields = ('status',)

    def validate_status(self, value):
        valid_transitions = {
            'open': ['actioned', 'dismissed'],
            'actioned': [],
            'dismissed': [],
        }
        current_status = self.instance.status
        if value not in valid_transitions.get(current_status, []):
            raise serializers.ValidationError(f"Cannot transition from {current_status} to {value}.")
        return value

from rest_framework import serializers
from ads.models import Ad

class PublicAdSerializer(serializers.ModelSerializer):
    advertiser_name = serializers.CharField(source='advertiser.company_name', read_only=True)

    class Meta:
        model = Ad
        fields = ('id', 'title', 'image_url', 'target_url', 'position', 'headline', 'subheadline', 'slot_index', 'advertiser_name', 'labels', 'show_in_hero')

from rest_framework import serializers
from .models import WebhookEvent


class WebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEvent
        fields = ['id', 'repo_name', 'commit_hash', 'received_at', 'processed']

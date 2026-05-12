import pytest
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from unittest.mock import patch

User = get_user_model()


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='webhook_test@example.com',
        username='webhookuser',
        password='testpass123'
    )


@pytest.mark.django_db
class TestGitHubWebhook:
    def test_valid_webhook_with_signature(self, user):
        import hmac
        import hashlib
        from django.conf import settings
        
        payload = b'{"action": "push", "repository": {"full_name": "test/repo"}, "after": "abc123"}'
        signature = 'sha256=' + hmac.new(
            settings.GITHUB_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        client = APIClient()
        client.credentials(HTTP_X_GITHUB_TOKEN=user.github_token or '')
        
        response = client.post(
            '/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature,
            HTTP_X_GITHUB_EVENT='push'
        )
        
        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_invalid_signature(self, client):
        response = client.post(
            '/webhooks/github/',
            data=b'{}',
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=invalid',
            HTTP_X_GITHUB_EVENT='push'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

import hmac
import hashlib
import json
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from .models import WebhookEvent
from .serializers import WebhookEventSerializer
from apps.reviews.models import Review
from apps.reviews.tasks import process_review


class GitHubWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.body
        signature = request.headers.get('X-Hub-Signature-256')
        event = request.headers.get('X-GitHub-Event')
        
        if not self._verify_signature(payload, signature):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN)
        
        if event != 'push':
            return Response({'message': 'Event ignored'}, status=status.HTTP_200_OK)
        
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return Response({'error': 'Invalid JSON payload'}, status=status.HTTP_400_BAD_REQUEST)
        
        repo_name = data.get('repository', {}).get('full_name', '')
        commit_hash = data.get('after', '')
        
        patches = data.get('patch', [])
        if not patches:
            return Response({'message': 'No code changes in push'}, status=status.HTTP_200_OK)
        
        diff = '\n'.join(patches)
        
       	from apps.users.models import User
        user = None
        if request.META.get('HTTP_X_GITHUB_TOKEN'):
            token = request.META['HTTP_X_GITHUB_TOKEN']
            user = User.objects.filter(github_token=token).first()
        
        if not user:
            return Response({'error': 'User not authenticated'}, status=status.HTTP_401_UNAUTHORIZED)
        
        webhook_event = WebhookEvent.objects.create(
            user=user,
            repo_name=repo_name,
            commit_hash=commit_hash,
            diff=diff
        )
        
        review = Review.objects.create(
            user=user,
            code=diff,
            language='diff'
        )
        
        webhook_event.review = review
        webhook_event.save()
        
        process_review.delay(review.id)
        
        return Response({
            'message': 'Webhook received',
            'review_id': review.id
        }, status=status.HTTP_202_ACCEPTED)

    def _verify_signature(self, payload, signature):
        if not signature or not settings.GITHUB_WEBHOOK_SECRET:
            return True
        
        if not signature.startswith('sha256='):
            return False
        
        expected_signature = 'sha256=' + hmac.new(
            settings.GITHUB_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)

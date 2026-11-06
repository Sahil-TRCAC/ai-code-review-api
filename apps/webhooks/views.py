import hashlib
import hmac
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RepositoryLinkError, TrackedRepository, WebhookEvent
from .tasks import fetch_webhook_diff

ZERO_SHA = '0' * 40


class MalformedPayload(Exception):
    pass


class GitHubWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.body
        signature = request.headers.get('X-Hub-Signature-256')
        event_name = request.headers.get('X-GitHub-Event')

        if not self._verify_signature(payload, signature):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN)

        if event_name != 'push':
            return Response({'message': 'Event ignored'}, status=status.HTTP_200_OK)

        try:
            data = json.loads(payload)
            changes = _changed_files(data)
        except json.JSONDecodeError:
            return Response({'error': 'Invalid JSON payload'}, status=status.HTTP_400_BAD_REQUEST)
        except MalformedPayload:
            return Response({'error': 'Malformed payload'}, status=status.HTTP_400_BAD_REQUEST)

        if not _should_fetch(data, changes):
            return Response({'message': 'No code changes in push'}, status=status.HTTP_200_OK)

        repo_name = _repository_name(data)
        if not repo_name:
            return Response({'error': 'Missing repository name'}, status=status.HTTP_400_BAD_REQUEST)

        commit_hash = _commit_hash(data)
        if not commit_hash:
            return Response({'error': 'Missing commit hash'}, status=status.HTTP_400_BAD_REQUEST)

        delivery_id = (request.headers.get('X-GitHub-Delivery') or '').strip()
        if not delivery_id:
            return Response({'error': 'Missing X-GitHub-Delivery'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            tracked = TrackedRepository.resolve(repo_name)
        except RepositoryLinkError:
            return Response({'error': 'Repository link is ambiguous'}, status=status.HTTP_409_CONFLICT)
        if tracked is None:
            return Response(
                {'error': 'Repository is not linked to a user'},
                status=status.HTTP_404_NOT_FOUND,
            )

        event, created = _claim_delivery(
            tracked=tracked,
            repo_name=repo_name,
            commit_hash=commit_hash,
            before_sha=_sha_or_blank(data.get('before')),
            default_branch=_default_branch(data),
            delivery_id=delivery_id,
        )
        if not created and _delivery_finished(event):
            return Response({
                'message': 'Delivery already processed',
                'webhook_event_id': event.id,
            }, status=status.HTTP_200_OK)

        return Response({
            'message': 'Webhook received',
            'webhook_event_id': event.id,
        }, status=status.HTTP_202_ACCEPTED)

    def _verify_signature(self, payload, signature):
        secret = settings.GITHUB_WEBHOOK_SECRET or ''
        if not secret or not signature or not signature.startswith('sha256='):
            return False

        expected = 'sha256=' + hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        try:
            return hmac.compare_digest(expected, signature)
        except Exception:
            return False


def _delivery_finished(event):
    return bool(event.review_id) or (event.processed and not event.fetch_error)


def _claim_delivery(tracked, repo_name, commit_hash, before_sha, default_branch, delivery_id):
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                user=tracked.user,
                repo_name=repo_name,
                commit_hash=commit_hash,
                before_sha=before_sha,
                default_branch=default_branch,
                diff='',
                delivery_id=delivery_id,
            )
            event_id = event.id
            transaction.on_commit(lambda: fetch_webhook_diff.delay(event_id))
        return event, True
    except IntegrityError:
        event = WebhookEvent.objects.filter(delivery_id=delivery_id).first()
        if event is None:
            raise

    if _delivery_finished(event):
        return event, False

    with transaction.atomic():
        event = WebhookEvent.objects.get(pk=event.pk)
        if _delivery_finished(event):
            return event, False
        event.processed = False
        event.fetch_error = ''
        event.save(update_fields=['processed', 'fetch_error'])
        event_id = event.id
        transaction.on_commit(lambda: fetch_webhook_diff.delay(event_id))
    return event, False


def _sha_or_blank(value):
    if isinstance(value, str) and len(value) <= 40:
        return value
    return ''


def _default_branch(data):
    repository = data.get('repository')
    if not isinstance(repository, dict):
        return ''
    branch = repository.get('default_branch')
    if not isinstance(branch, str):
        return ''
    return branch.strip()[:255]


def _repository_name(data):
    repository = data.get('repository')
    if not isinstance(repository, dict):
        return ''
    full_name = repository.get('full_name')
    if not isinstance(full_name, str):
        return ''
    full_name = full_name.strip()
    owner, separator, name = full_name.partition('/')
    if not separator or not owner or not name or '/' in name or ' ' in full_name:
        return ''
    return full_name


def _commit_hash(data):
    after = data.get('after')
    if isinstance(after, str) and after and after != ZERO_SHA and len(after) <= 40:
        return after
    commits = data.get('commits')
    if isinstance(commits, list):
        for commit in reversed(commits):
            if isinstance(commit, dict):
                commit_id = commit.get('id')
                if isinstance(commit_id, str) and commit_id and len(commit_id) <= 40:
                    return commit_id
    return ''


def _changed_files(data):
    if not isinstance(data, dict):
        raise MalformedPayload()

    commits = data.get('commits', None)
    if commits is None:
        head = data.get('head_commit')
        commits = [head] if isinstance(head, dict) else []
    elif not isinstance(commits, list):
        raise MalformedPayload()

    added, modified, removed = set(), set(), set()
    buckets = {'added': added, 'modified': modified, 'removed': removed}
    for commit in commits:
        if not isinstance(commit, dict):
            raise MalformedPayload()
        for key, bucket in buckets.items():
            files = commit.get(key, [])
            if files is None:
                continue
            if not isinstance(files, list) or any(not isinstance(name, str) for name in files):
                raise MalformedPayload()
            bucket.update(files)
    return {'added': added, 'modified': modified, 'removed': removed}


def _should_fetch(data, changes):
    if changes['added'] or changes['modified'] or changes['removed']:
        return True
    before = data.get('before')
    after = data.get('after')
    before_ok = isinstance(before, str) and before and before != ZERO_SHA
    after_ok = isinstance(after, str) and after and after != ZERO_SHA
    if before_ok and after_ok and before != after:
        return True
    return isinstance(before, str) and before == ZERO_SHA and after_ok

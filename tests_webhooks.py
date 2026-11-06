import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.test import APIClient

from apps.reviews.models import Review
from apps.webhooks.models import TrackedRepository, WebhookEvent
from apps.webhooks.tasks import PARTIAL_NOTE, ZERO_SHA, fetch_webhook_diff

User = get_user_model()
SECRET = 'test-webhook-secret'
BEFORE = 'a' * 40
AFTER = 'b' * 40
PATCH = "@@ -0,0 +1 @@\n+print('hi')"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='webhook_test@example.com',
        username='webhookuser',
        password='testpass123',
        github_token='ghp_test_token',
    )


@pytest.fixture
def github_secret(settings):
    settings.GITHUB_WEBHOOK_SECRET = SECRET
    return SECRET


@pytest.fixture
def tracked_repo(user):
    return TrackedRepository.objects.create(user=user, full_name='test/repo')


def _sign(body, secret=SECRET):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return 'sha256=' + digest


def _push_payload(**overrides):
    payload = {
        'ref': 'refs/heads/main',
        'before': BEFORE,
        'after': AFTER,
        'repository': {'full_name': 'test/repo', 'default_branch': 'main'},
        'commits': [{
            'id': AFTER,
            'added': ['app.py'],
            'modified': ['readme.md'],
            'removed': ['old.py'],
        }],
    }
    payload.update(overrides)
    return payload


def _post(client, body, secret=SECRET, signature=None, event='push', delivery='delivery-1', **headers):
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    extra = {
        'HTTP_X_GITHUB_EVENT': event,
        'content_type': 'application/json',
    }
    if signature is not False:
        extra['HTTP_X_HUB_SIGNATURE_256'] = signature if signature is not None else _sign(raw, secret)
    if delivery is not None:
        extra['HTTP_X_GITHUB_DELIVERY'] = delivery
    extra.update(headers)
    return client.post('/webhooks/github/', data=raw, **extra)


def _github_response(payload, code=200, headers=None):
    response = MagicMock()
    response.status_code = code
    response.headers = headers or {}
    response.json.return_value = payload
    return response


def _file(name='app.py', patch=PATCH):
    entry = {'filename': name}
    if patch is not None:
        entry['patch'] = patch
    return entry


def _event(user, **kwargs):
    values = {
        'user': user,
        'repo_name': 'test/repo',
        'commit_hash': AFTER,
        'before_sha': BEFORE,
        'default_branch': 'main',
        'diff': '',
        'delivery_id': 'delivery-task',
    }
    values.update(kwargs)
    return WebhookEvent.objects.create(**values)


@pytest.mark.django_db
class TestGitHubWebhook:
    def test_valid_push_returns_202_without_calling_github(self, client, github_secret, tracked_repo, django_capture_on_commit_callbacks):
        with patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay, \
                patch('apps.webhooks.tasks.requests.get') as get:
            with django_capture_on_commit_callbacks(execute=True):
                response = _post(client, _push_payload())

        assert response.status_code == status.HTTP_202_ACCEPTED
        event = WebhookEvent.objects.get(id=response.data['webhook_event_id'])
        assert event.delivery_id == 'delivery-1'
        assert event.user == tracked_repo.user
        assert event.before_sha == BEFORE
        assert event.default_branch == 'main'
        assert event.diff == ''
        assert event.review_id is None
        assert Review.objects.count() == 0
        delay.assert_called_once_with(event.id)
        get.assert_not_called()

    def test_repository_name_match_is_case_insensitive(self, client, github_secret, user, django_capture_on_commit_callbacks):
        TrackedRepository.objects.create(user=user, full_name='Test/Repo')
        payload = _push_payload()
        payload['repository'] = {'full_name': 'test/repo', 'default_branch': 'main'}
        with patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay:
            with django_capture_on_commit_callbacks(execute=True):
                response = _post(client, payload)
        assert response.status_code == status.HTTP_202_ACCEPTED
        event = WebhookEvent.objects.get(id=response.data['webhook_event_id'])
        assert event.user == user
        delay.assert_called_once_with(event.id)

    def test_case_insensitive_repository_name_is_unique(self, user):
        other = User.objects.create_user(
            email='other@example.com',
            username='otheruser',
            password='testpass123',
        )
        TrackedRepository.objects.create(user=user, full_name='Test/Repo')
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TrackedRepository.objects.create(user=other, full_name='test/repo')

    def test_missing_secret_is_rejected(self, client, settings):
        settings.GITHUB_WEBHOOK_SECRET = ''
        response = _post(client, _push_payload(), secret='')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert WebhookEvent.objects.count() == 0

    def test_missing_signature_is_rejected(self, client, github_secret):
        response = _post(client, _push_payload(), signature=False)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_invalid_signature(self, client, github_secret):
        response = _post(client, b'{}', signature='sha256=invalid')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_signature_requires_sha256_prefix(self, client, github_secret):
        raw = json.dumps(_push_payload()).encode()
        digest = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        response = _post(client, raw, signature=digest)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_push_without_file_changes_does_not_queue(self, client, github_secret, tracked_repo):
        payload = _push_payload(before=AFTER, commits=[{
            'id': AFTER, 'added': [], 'modified': [], 'removed': [],
        }])
        with patch('apps.webhooks.tasks.requests.get') as get, \
                patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay:
            response = _post(client, payload)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'No code changes in push'
        assert WebhookEvent.objects.count() == 0
        get.assert_not_called()
        delay.assert_not_called()

    def test_distinct_shas_without_listed_files_are_still_queued(self, client, github_secret, tracked_repo, django_capture_on_commit_callbacks):
        payload = _push_payload(commits=[{
            'id': AFTER, 'added': [], 'modified': [], 'removed': [],
        }])
        with patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay, \
                patch('apps.webhooks.tasks.requests.get') as get:
            with django_capture_on_commit_callbacks(execute=True):
                response = _post(client, payload)
        assert response.status_code == status.HTTP_202_ACCEPTED
        get.assert_not_called()
        delay.assert_called_once()

    def test_legacy_fixture_without_patch_has_no_changes(self, client, github_secret):
        payload = {'action': 'push', 'repository': {'full_name': 'test/repo'}, 'after': 'abc123'}
        with patch('apps.webhooks.tasks.requests.get') as get:
            response = _post(client, payload)
        assert response.status_code == status.HTTP_200_OK
        assert WebhookEvent.objects.count() == 0
        get.assert_not_called()

    def test_malformed_payload(self, client, github_secret):
        assert _post(client, b'not-json').status_code == status.HTTP_400_BAD_REQUEST
        assert _post(client, b'[]').status_code == status.HTTP_400_BAD_REQUEST
        assert _post(client, {'repository': 'test/repo', 'commits': 'bad'}).status_code == status.HTTP_400_BAD_REQUEST
        assert WebhookEvent.objects.count() == 0

    def test_unlinked_repository_ignores_custom_token_header(self, client, github_secret, user):
        with patch('apps.webhooks.tasks.requests.get') as get:
            response = _post(client, _push_payload(), HTTP_X_GITHUB_TOKEN=user.github_token)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert WebhookEvent.objects.count() == 0
        get.assert_not_called()

    def test_duplicate_delivery_with_review_does_not_queue_again(self, client, github_secret, tracked_repo, user):
        review = Review.objects.create(user=user, code='already', language='diff')
        WebhookEvent.objects.create(
            user=user,
            repo_name='test/repo',
            commit_hash=AFTER,
            diff='already',
            delivery_id='delivery-done',
            review=review,
            processed=True,
        )
        with patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay:
            response = _post(client, _push_payload(), delivery='delivery-done')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['message'] == 'Delivery already processed'
        assert WebhookEvent.objects.filter(delivery_id='delivery-done').count() == 1
        assert Review.objects.count() == 1
        delay.assert_not_called()

    def test_failed_delivery_can_be_recovered(self, client, github_secret, tracked_repo, user, django_capture_on_commit_callbacks):
        event = WebhookEvent.objects.create(
            user=user,
            repo_name='test/repo',
            commit_hash=AFTER,
            delivery_id='delivery-failed',
            processed=True,
            fetch_error='GitHub request timed out',
        )
        with patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay:
            with django_capture_on_commit_callbacks(execute=True):
                response = _post(client, _push_payload(), delivery='delivery-failed')
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert WebhookEvent.objects.filter(delivery_id='delivery-failed').count() == 1
        event.refresh_from_db()
        assert event.fetch_error == ''
        assert event.review_id is None
        delay.assert_called_once_with(event.id)

    def test_unrelated_integrity_error_is_not_treated_as_a_duplicate(self, client, github_secret, tracked_repo):
        with patch('apps.webhooks.views.WebhookEvent.objects.create', side_effect=IntegrityError('other')):
            with pytest.raises(IntegrityError):
                _post(client, _push_payload())


@pytest.mark.django_db
class TestOverlappingDeliveries:
    def test_delivery_id_unique_constraint(self, user):
        _event(user, delivery_id='delivery-unique')
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _event(user, delivery_id='delivery-unique')
        assert WebhookEvent.objects.filter(delivery_id='delivery-unique').count() == 1

    def test_overlapping_inserts_keep_one_event(self, client, github_secret, tracked_repo, django_capture_on_commit_callbacks):
        real_create = WebhookEvent.objects.create
        state = {'created': False}

        def racing_create(**kwargs):
            if not state['created']:
                state['created'] = True
                return real_create(**kwargs)
            raise IntegrityError('UNIQUE constraint failed: webhook_events.delivery_id')

        with patch('apps.webhooks.views.WebhookEvent.objects.create', side_effect=racing_create), \
                patch('apps.webhooks.views.fetch_webhook_diff.delay') as delay:
            with django_capture_on_commit_callbacks(execute=True):
                first = _post(client, _push_payload(), delivery='delivery-race')
                second = _post(client, _push_payload(), delivery='delivery-race')

        assert first.status_code == status.HTTP_202_ACCEPTED
        assert second.status_code == status.HTTP_202_ACCEPTED
        assert WebhookEvent.objects.filter(delivery_id='delivery-race').count() == 1
        assert Review.objects.count() == 0
        event = WebhookEvent.objects.get(delivery_id='delivery-race')
        assert delay.call_count == 2
        assert all(call.args == (event.id,) for call in delay.call_args_list)


@pytest.mark.django_db
class TestFetchWebhookDiff:
    def test_task_saves_diff_creates_review_and_queues_llm(self, user, tracked_repo, django_capture_on_commit_callbacks):
        event = _event(user)
        github = _github_response({'files': [_file()]})
        with patch('apps.webhooks.tasks.requests.get', return_value=github) as get, \
                patch('apps.webhooks.tasks.process_review.delay') as delay:
            with django_capture_on_commit_callbacks(execute=True):
                result = fetch_webhook_diff(event.id)

        event.refresh_from_db()
        review = Review.objects.get(id=result['review_id'])
        assert event.review_id == review.id
        assert event.processed is True
        assert event.diff_partial is False
        assert "print('hi')" in event.diff
        assert review.code == event.diff
        assert review.user == user
        delay.assert_called_once_with(review.id)
        assert get.call_args.kwargs['allow_redirects'] is False
        assert get.call_args.kwargs['headers']['Authorization'] == 'Bearer ghp_test_token'
        assert f'/compare/{BEFORE}...{AFTER}' in get.call_args.args[0]

    def test_second_task_run_does_not_create_another_review(self, user, tracked_repo, django_capture_on_commit_callbacks):
        event = _event(user)
        github = _github_response({'files': [_file()]})
        with patch('apps.webhooks.tasks.requests.get', return_value=github), \
                patch('apps.webhooks.tasks.process_review.delay'):
            with django_capture_on_commit_callbacks(execute=True):
                fetch_webhook_diff(event.id)
                fetch_webhook_diff(event.id)
        assert Review.objects.count() == 1

    def test_new_branch_compares_against_default_branch(self, user, tracked_repo):
        event = _event(user, before_sha=ZERO_SHA, default_branch='main', delivery_id='new-branch')
        github = _github_response({'files': [_file()]})
        with patch('apps.webhooks.tasks.requests.get', return_value=github) as get, \
                patch('apps.webhooks.tasks.process_review.delay'):
            fetch_webhook_diff(event.id)
        assert f'/compare/main...{AFTER}' in get.call_args.args[0]
        assert '/commits/' not in get.call_args.args[0]

    def test_first_commit_falls_back_to_the_commit_diff(self, user, tracked_repo, django_capture_on_commit_callbacks):
        event = _event(user, before_sha=ZERO_SHA, default_branch='main', delivery_id='first-commit')
        empty = _github_response({'files': []})
        commit = _github_response({'files': [_file()]})
        with patch('apps.webhooks.tasks.requests.get', side_effect=[empty, commit]) as get, \
                patch('apps.webhooks.tasks.process_review.delay'):
            with django_capture_on_commit_callbacks(execute=True):
                fetch_webhook_diff(event.id)
        urls = [call.args[0] for call in get.call_args_list]
        assert f'/compare/main...{AFTER}' in urls[0]
        assert f'/commits/{AFTER}' in urls[1]
        event.refresh_from_db()
        assert event.review_id is not None
        assert event.diff_partial is False

    def test_partial_diff_is_marked(self, user, tracked_repo, django_capture_on_commit_callbacks):
        event = _event(user, delivery_id='partial')
        github = _github_response({'files': [_file(), _file('image.png', patch=None)]})
        with patch('apps.webhooks.tasks.requests.get', return_value=github), \
                patch('apps.webhooks.tasks.process_review.delay'):
            with django_capture_on_commit_callbacks(execute=True):
                fetch_webhook_diff(event.id)
        event.refresh_from_db()
        assert event.diff_partial is True
        assert event.diff.startswith(PARTIAL_NOTE)
        assert event.review_id is not None

    def test_timeout_is_retried(self, user, tracked_repo):
        event = _event(user, delivery_id='timeout')
        with patch('apps.webhooks.tasks.requests.get', side_effect=requests.Timeout('slow')):
            fetch_webhook_diff.push_request(retries=0, called_directly=False, is_eager=True)
            try:
                with pytest.raises(Retry):
                    fetch_webhook_diff.run(event.id)
            finally:
                fetch_webhook_diff.pop_request()
        event.refresh_from_db()
        assert event.processed is False
        assert event.review_id is None

    def test_retry_after_is_used_for_rate_limits(self, user, tracked_repo):
        event = _event(user, delivery_id='rate-limit')
        github = _github_response({}, code=429, headers={'Retry-After': '12'})
        with patch('apps.webhooks.tasks.requests.get', return_value=github):
            fetch_webhook_diff.push_request(retries=0, called_directly=False, is_eager=True)
            try:
                with pytest.raises(Retry) as caught:
                    fetch_webhook_diff.run(event.id)
            finally:
                fetch_webhook_diff.pop_request()
        assert caught.value.when == 12
        event.refresh_from_db()
        assert event.processed is False

    def test_retry_exhaustion_records_the_failure(self, user, tracked_repo):
        event = _event(user, delivery_id='give-up')
        with patch('apps.webhooks.tasks.requests.get', side_effect=requests.Timeout('slow')):
            fetch_webhook_diff.push_request(retries=3, called_directly=False, is_eager=True)
            try:
                fetch_webhook_diff.run(event.id)
            finally:
                fetch_webhook_diff.pop_request()
        event.refresh_from_db()
        assert event.processed is True
        assert 'timed out' in event.fetch_error
        assert event.review_id is None

    def test_authentication_failure_is_not_retried(self, user, tracked_repo):
        event = _event(user, delivery_id='auth')
        github = _github_response({}, code=401)
        with patch('apps.webhooks.tasks.requests.get', return_value=github) as get:
            fetch_webhook_diff.push_request(retries=0, called_directly=False, is_eager=True)
            try:
                fetch_webhook_diff.run(event.id)
            finally:
                fetch_webhook_diff.pop_request()
        assert get.call_count == 1
        event.refresh_from_db()
        assert event.processed is True
        assert '401' in event.fetch_error
        assert Review.objects.count() == 0

    def test_redirect_is_not_followed(self, user, tracked_repo):
        event = _event(user, delivery_id='redirect')
        github = _github_response({}, code=302, headers={'Location': 'https://example.test/steal'})
        with patch('apps.webhooks.tasks.requests.get', return_value=github) as get:
            fetch_webhook_diff(event.id)
        assert get.call_args.kwargs['allow_redirects'] is False
        event.refresh_from_db()
        assert event.processed is True
        assert 'redirect' in event.fetch_error.lower()
        assert Review.objects.count() == 0

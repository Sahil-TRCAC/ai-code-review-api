from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from apps.reviews.models import Review, ReviewStatus, Bug, QualitySuggestion
from apps.reviews.tasks import process_review

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='test@example.com',
        username='testuser',
        password='testpass123'
    )


@pytest.fixture
def authenticated_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestAuthAPI:
    def test_user_registration(self, api_client):
        response = api_client.post('/api/auth/register/', {
            'email': 'newuser@example.com',
            'username': 'newuser',
            'password': 'SecurePass123!',
            'password_confirm': 'SecurePass123!'
        })
        assert response.status_code == status.HTTP_201_CREATED

    def test_user_login(self, api_client, user):
        response = api_client.post('/api/auth/login/', {
            'email': user.email,
            'password': 'testpass123'
        })
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert 'refresh' in response.data

    def test_refresh_access_token(self, api_client, user):
        login = api_client.post('/api/auth/login/', {
            'email': user.email,
            'password': 'testpass123'
        })
        response = api_client.post('/api/auth/refresh/', {
            'refresh': login.data['refresh']
        })
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data

    def test_profile_access(self, authenticated_client):
        response = authenticated_client.get('/api/auth/profile/')
        assert response.status_code == status.HTTP_200_OK
        assert 'github_token' not in response.data

    def test_profile_update_does_not_return_github_token(self, authenticated_client, user):
        response = authenticated_client.patch('/api/auth/profile/', {
            'github_token': 'ghp_secret_value',
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert 'github_token' not in response.data
        assert 'ghp_secret_value' not in str(response.data)
        user.refresh_from_db()
        assert user.github_token == 'ghp_secret_value'
        profile = authenticated_client.get('/api/auth/profile/')
        assert 'github_token' not in profile.data


@pytest.mark.django_db
class TestReviewAPI:
    def test_get_review_detail(self, authenticated_client, user):
        review = Review.objects.create(
            user=user,
            code='test code',
            language='python',
            status=ReviewStatus.COMPLETED,
            summary='Test summary',
            score=85
        )
        response = authenticated_client.get(f'/api/review/{review.id}/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == review.id
        assert response.data['status'] == ReviewStatus.COMPLETED
        assert response.data['summary'] == 'Test summary'
        assert response.data['score'] == 85

    def test_list_reviews(self, authenticated_client, user):
        Review.objects.create(user=user, code='test1', language='python')
        Review.objects.create(user=user, code='test2', language='javascript')
        response = authenticated_client.get('/api/reviews/')
        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data
        assert len(response.data['results']) == 2


@pytest.mark.django_db
class TestRateLimiting:
    def test_rate_limit_exceeded(self, user, api_client):
        from django.utils import timezone
        user.daily_review_count = 10
        user.daily_review_reset = timezone.now() + timezone.timedelta(days=1)
        user.save()
        
        api_client.force_authenticate(user=user)
        response = api_client.post('/api/review/', {
            'code': 'print("hello")',
            'language': 'python'
        }, format='json')
        
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert 'Rate limit exceeded' in response.data.get('error', '')

    def test_rate_limit_follows_settings(self, user, api_client, settings):
        from django.utils import timezone
        settings.DAILY_REVIEW_LIMIT = 2
        user.daily_review_count = 2
        user.daily_review_reset = timezone.now() + timezone.timedelta(days=1)
        user.save()

        api_client.force_authenticate(user=user)
        response = api_client.post('/api/review/', {
            'code': 'print("hello")',
            'language': 'python'
        }, format='json')
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.django_db
class TestAdminAPI:
    def test_admin_stats_requires_staff(self, authenticated_client):
        response = authenticated_client.get('/api/admin/stats/')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_stats_access(self, user, api_client):
        user.is_staff = True
        user.save()
        api_client.force_authenticate(user=user)
        
        response = api_client.get('/api/admin/stats/')
        assert response.status_code == status.HTTP_200_OK
        assert 'reviews_today' in response.data


def _review_user():
    return User.objects.create_user(
        email='queue@example.com',
        username='queueuser',
        password='testpass123',
    )


LLM_RESULT = {
    'summary': 'Solid function with a small issue',
    'score': 88,
    'bugs': [{'line': 1, 'issue': 'missing return type', 'severity': 'low'}],
    'security': [{'line': 2, 'issue': 'unchecked input', 'severity': 'high'}],
    'quality': [{'suggestion': 'add a docstring', 'severity': 'medium'}],
}


@pytest.mark.django_db(transaction=True)
class TestReviewSubmissionQueue:
    def test_submit_review_queues_task_and_returns_pending(self):
        from django.conf import settings

        user = _review_user()
        client = APIClient()
        client.force_authenticate(user=user)
        async_result = MagicMock()
        async_result.id = 'celery-task-123'

        with patch('apps.reviews.views.process_review.delay', return_value=async_result) as delay, \
                patch('apps.reviews.llm_service.LLMService.review_code') as review_code:
            response = client.post('/api/review/', {
                'code': 'def hello(): return "Hello"',
                'language': 'python',
            }, format='json')

        assert settings.CELERY_TASK_ALWAYS_EAGER is False
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data['status'] == ReviewStatus.PENDING
        review = Review.objects.get(id=response.data['id'])
        assert review.user == user
        assert review.code == 'def hello(): return "Hello"'
        assert review.language == 'python'
        assert review.status == ReviewStatus.PENDING
        assert review.celery_task_id == 'celery-task-123'
        delay.assert_called_once_with(review.id)
        review_code.assert_not_called()

    def test_queue_failure_marks_review_failed(self):
        user = _review_user()
        client = APIClient()
        client.force_authenticate(user=user)

        with patch('apps.reviews.views.process_review.delay', side_effect=ConnectionError('redis down')), \
                patch('apps.reviews.llm_service.LLMService.review_code') as review_code:
            response = client.post('/api/review/', {
                'code': 'print("hello")',
                'language': 'python',
            }, format='json')

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        review = Review.objects.get(id=response.data['id'])
        assert response.data['status'] == ReviewStatus.FAILED
        assert review.status == ReviewStatus.FAILED
        assert review.celery_task_id in (None, '')
        review_code.assert_not_called()


@pytest.mark.django_db
class TestProcessReviewTask:
    def test_success_marks_review_completed(self, user):
        review = Review.objects.create(user=user, code='x = 1', language='python')

        with patch('apps.reviews.tasks.LLMService') as llm:
            llm.return_value.review_code.return_value = LLM_RESULT
            result = process_review(review.id)

        review.refresh_from_db()
        assert result == {'review_id': review.id, 'status': 'completed'}
        assert review.status == ReviewStatus.COMPLETED
        assert review.summary == LLM_RESULT['summary']
        assert review.score == 88
        assert review.raw_llm_response == LLM_RESULT
        assert list(review.bugs.values('line', 'issue', 'severity')) == [
            {'line': 1, 'issue': 'missing return type', 'severity': 'low'}
        ]
        assert review.security_issues.count() == 1
        assert list(review.quality_suggestions.values('suggestion', 'severity')) == [
            {'suggestion': 'add a docstring', 'severity': 'medium'}
        ]
        assert Bug.objects.filter(review=review).count() == 1
        assert QualitySuggestion.objects.filter(review=review).count() == 1

    def test_terminal_failure_marks_review_failed(self, user):
        review = Review.objects.create(
            user=user,
            code='x = 1',
            language='python',
            status=ReviewStatus.PENDING,
        )

        with patch('apps.reviews.tasks.LLMService') as llm:
            llm.return_value.review_code.side_effect = RuntimeError('provider down')
            with pytest.raises(RuntimeError, match='provider down'):
                process_review(review.id)

        review.refresh_from_db()
        assert review.status == ReviewStatus.FAILED

    def test_failure_with_retry_pending_does_not_mark_failed(self, user):
        review = Review.objects.create(
            user=user,
            code='x = 1',
            language='python',
            status=ReviewStatus.PENDING,
        )

        with patch('apps.reviews.tasks.LLMService') as llm:
            llm.return_value.review_code.side_effect = RuntimeError('temporary')
            process_review.push_request(retries=0, called_directly=False, is_eager=True)
            try:
                with pytest.raises(Retry):
                    process_review.run(review.id)
            finally:
                process_review.pop_request()

        review.refresh_from_db()
        assert review.status == ReviewStatus.PROCESSING

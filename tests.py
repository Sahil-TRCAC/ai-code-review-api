import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from apps.reviews.models import Review, ReviewStatus

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

    def test_profile_access(self, authenticated_client):
        response = authenticated_client.get('/api/auth/profile/')
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestReviewAPI:
    def test_submit_review(self, authenticated_client, user):
        response = authenticated_client.post('/api/review/', {
            'code': 'def hello(): return "Hello"',
            'language': 'python'
        }, format='json')
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert 'id' in response.data

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

    def test_list_reviews(self, authenticated_client, user):
        Review.objects.create(user=user, code='test1', language='python')
        Review.objects.create(user=user, code='test2', language='javascript')
        response = authenticated_client.get('/api/reviews/')
        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data


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

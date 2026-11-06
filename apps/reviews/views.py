from django.conf import settings
from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from .models import Review, ReviewStatus, Bug
from .serializers import ReviewCreateSerializer, ReviewSerializer, ReviewListSerializer
from .tasks import process_review


def _check_rate_limit(user):
    if not user.check_and_increment_review_count():
        return False, user.daily_review_reset, 0
    return True, user.daily_review_reset, user.get_remaining_reviews()


class SubmitReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        allowed, reset_time, remaining = _check_rate_limit(request.user)
        
        if not allowed:
            return Response({
                'error': 'Rate limit exceeded',
                'detail': 'Daily review limit reached',
                'limit': settings.DAILY_REVIEW_LIMIT,
                'reset_at': reset_time.isoformat()
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        queue_state = {'error': None}

        with transaction.atomic():
            review = serializer.save(user=request.user, status=ReviewStatus.PENDING)
            review_id = review.id

            def enqueue():
                try:
                    async_result = process_review.delay(review_id)
                except Exception as exc:
                    queue_state['error'] = exc
                    Review.objects.filter(pk=review_id).update(status=ReviewStatus.FAILED)
                    return
                Review.objects.filter(pk=review_id).update(celery_task_id=str(async_result.id))

            transaction.on_commit(enqueue)

        review.refresh_from_db()

        if queue_state['error'] is not None:
            response = Response({
                'id': review.id,
                'status': review.status,
                'error': 'Failed to queue review for background processing',
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            response['X-RateLimit-Remaining'] = remaining - 1
            return response

        response = Response({
            'id': review.id,
            'status': review.status,
            'message': 'Review submitted. Processing in background.'
        }, status=status.HTTP_202_ACCEPTED)
        response['X-RateLimit-Remaining'] = remaining - 1
        return response


class ReviewDetailView(generics.RetrieveAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        return Review.objects.filter(user=self.request.user).prefetch_related('bugs', 'security_issues', 'quality_suggestions')


class ReviewListView(generics.ListAPIView):
    serializer_class = ReviewListSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['language', 'status']
    ordering_fields = ['created_at', 'score']
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = Review.objects.filter(user=self.request.user)
        
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)
        
        return queryset


class AdminStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)
        
        from datetime import datetime
        from django.db.models import Avg, Count
        from django.utils import timezone

        today = timezone.now().date()
        today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        
        reviews_today = Review.objects.filter(created_at__gte=today_start).count()
        avg_score = Review.objects.filter(status=ReviewStatus.COMPLETED).aggregate(Avg('score'))['score__avg']
        
        common_issues = Bug.objects.filter(
            review__created_at__gte=today_start
        ).values('issue').annotate(count=Count('id')).order_by('-count')[:10]
        
        return Response({
            'reviews_today': reviews_today,
            'average_score': round(avg_score, 2) if avg_score else 0,
            'most_common_issues': list(common_issues)
        })

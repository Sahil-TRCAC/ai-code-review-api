from django.conf import settings
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from .models import Review, ReviewStatus, Bug, SecurityIssue, QualitySuggestion
from .serializers import ReviewCreateSerializer, ReviewSerializer, ReviewListSerializer
from .llm_service import LLMService


class RateLimitMixin:
    def check_rate_limit(self, user):
        if not user.check_and_increment_review_count():
            from datetime import timedelta
            reset_time = user.daily_review_reset
            remaining = 0
            return False, reset_time, remaining
        remaining = user.get_remaining_reviews()
        reset_time = user.daily_review_reset
        return True, reset_time, remaining


class SubmitReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        rate_check = RateLimitMixin()
        allowed, reset_time, remaining = rate_check.check_rate_limit(request.user)
        
        if not allowed:
            return Response({
                'error': 'Rate limit exceeded',
                'detail': 'Daily review limit reached',
                'limit': settings.DAILY_REVIEW_LIMIT,
                'reset_at': reset_time.isoformat()
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = serializer.save(user=request.user)
        
        review.status = ReviewStatus.PROCESSING
        review.save()
        
        try:
            llm = LLMService()
            result = llm.review_code(review.code, review.language)
            
            review.summary = result.get('summary', '')
            review.score = result.get('score', 0)
            review.raw_llm_response = result
            
            Bug.objects.filter(review=review).delete()
            for bug_data in result.get('bugs', []):
                Bug.objects.create(review=review, line=bug_data.get('line'), issue=bug_data.get('issue', ''), severity=bug_data.get('severity', 'low'))
            
            SecurityIssue.objects.filter(review=review).delete()
            for sec_data in result.get('security', []):
                SecurityIssue.objects.create(review=review, line=sec_data.get('line'), issue=sec_data.get('issue', ''), severity=sec_data.get('severity', 'medium'))
            
            QualitySuggestion.objects.filter(review=review).delete()
            for qual_data in result.get('quality', []):
                QualitySuggestion.objects.create(review=review, suggestion=qual_data.get('suggestion', ''), severity=qual_data.get('severity', 'low'))
            
            review.status = ReviewStatus.COMPLETED
        except Exception as e:
            review.status = ReviewStatus.FAILED
        
        review.save()
        review.save()
        
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
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
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
        
        from django.db.models import Count, Avg
        from django.utils import timezone
        from datetime import timedelta
        
        today = timezone.now().date()
        today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
        
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

from django.urls import path
from .views import SubmitReviewView, ReviewDetailView, ReviewListView, AdminStatsView

urlpatterns = [
    path('review/', SubmitReviewView.as_view(), name='submit_review'),
    path('review/<int:id>/', ReviewDetailView.as_view(), name='review_detail'),
    path('reviews/', ReviewListView.as_view(), name='review_list'),
    path('admin/stats/', AdminStatsView.as_view(), name='admin_stats'),
]

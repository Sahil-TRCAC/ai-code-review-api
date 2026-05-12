from django.urls import path
from .views import GitHubWebhookView

urlpatterns = [
    path('github/', GitHubWebhookView.as_view(), name='github_webhook'),
]

from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from rest_framework_simplejwt.views import TokenRefreshView

def health(request):
    return JsonResponse({"status": "ok", "message": "AI Code Review API is live"})

urlpatterns = [
    path('', health),
    path('admin/', admin.site.urls),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/', include('apps.users.urls')),
    path('api/', include('apps.reviews.urls')),
    path('webhooks/', include('apps.webhooks.urls')),
]

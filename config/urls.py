from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/', include('apps.users.urls')),
    path('api/', include('apps.reviews.urls')),
    path('webhooks/', include('apps.webhooks.urls')),
]

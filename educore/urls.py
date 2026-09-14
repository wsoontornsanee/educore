"""URL configuration for educore project."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    # Base API route: /api/v1/ per spec/01 §8.1
    path('api/v1/', include('apps.identity.urls')),
    path('api/v1/', include('apps.foundation.urls')),
]

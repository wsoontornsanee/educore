"""Web (session-auth) URL routes for apps.core — mounted under /web/."""
from django.urls import path

from .views import ComingSoonView

app_name = 'console'

urlpatterns = [
    path('coming-soon/', ComingSoonView.as_view(), name='coming_soon'),
]

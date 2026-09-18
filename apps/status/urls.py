"""URL routes for the public service status page (no auth, no /api/v1 prefix)."""
from django.urls import path

from . import views

app_name = 'status'

urlpatterns = [
    path('', views.StatusPageView.as_view(), name='page'),
    path('subscribe/', views.StatusSubscribeView.as_view(), name='subscribe'),
]

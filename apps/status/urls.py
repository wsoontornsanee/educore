"""URL routes for the public service status page (no auth, no /api/v1 prefix)."""
from django.urls import path

from . import views

app_name = 'status'

urlpatterns = [
    path('', views.StatusPageView.as_view(), name='page'),
    # Temporary stub so the template's {% url 'status:subscribe' %} resolves
    # before Task 10 implements the real subscribe view. Task 10 replaces
    # this line with its own StatusSubscribeView.
    path('subscribe/', views.StatusPageView.as_view(), name='subscribe'),
]

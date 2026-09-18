"""Web (session-auth HTMX) routes for apps.wallet — mounted under /web/wallet/."""
from django.urls import path

from .web_views import CanteenConsolePageView

urlpatterns = [
    path('canteen/', CanteenConsolePageView.as_view(), name='canteen-console-page'),
]

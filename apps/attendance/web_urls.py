"""Web (session-auth HTMX) routes for apps.attendance — mounted under /web/attendance/."""
from django.urls import path

from .web_views import GateConsoleFeedView, GateConsolePageView

urlpatterns = [
    path('gate/', GateConsolePageView.as_view(), name='attendance-gate-console-page'),
    path('gate/feed/', GateConsoleFeedView.as_view(), name='attendance-gate-console-feed'),
]

"""Web (session-auth HTMX) routes for apps.attendance — mounted under /web/attendance/."""
from django.urls import path

from .web_views import GateConsoleFeedView, GateConsolePageView, GateDayOverrideView, GateManualCheckinView

urlpatterns = [
    path('gate/', GateConsolePageView.as_view(), name='attendance-gate-console-page'),
    path('gate/manual/', GateManualCheckinView.as_view(), name='attendance-gate-console-manual'),
    path('gate/override/', GateDayOverrideView.as_view(), name='attendance-gate-console-override'),
    path('gate/feed/', GateConsoleFeedView.as_view(), name='attendance-gate-console-feed'),
]

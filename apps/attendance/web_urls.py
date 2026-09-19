"""Web (session-auth HTMX) routes for apps.attendance — mounted under /web/attendance/."""
from django.urls import path

from .pickup_web_views import (
    PickupConsolePageView, PickupOverrideWebView, PickupReleaseWebView, PickupRevokeWebView, PickupVerifyWebView,
)
from .web_views import GateConsoleFeedView, GateConsolePageView, GateDayOverrideView, GateManualCheckinView

urlpatterns = [
    path('gate/', GateConsolePageView.as_view(), name='attendance-gate-console-page'),
    path('gate/manual/', GateManualCheckinView.as_view(), name='attendance-gate-console-manual'),
    path('gate/override/', GateDayOverrideView.as_view(), name='attendance-gate-console-override'),
    path('pickup/', PickupConsolePageView.as_view(), name='attendance-pickup-console-page'),
    path('pickup/verify/', PickupVerifyWebView.as_view(), name='attendance-pickup-console-verify'),
    path('pickup/release/', PickupReleaseWebView.as_view(), name='attendance-pickup-console-release'),
    path('pickup/revoke/', PickupRevokeWebView.as_view(), name='attendance-pickup-console-revoke'),
    path('pickup/override/', PickupOverrideWebView.as_view(), name='attendance-pickup-console-override'),
    path('gate/feed/', GateConsoleFeedView.as_view(), name='attendance-gate-console-feed'),
]

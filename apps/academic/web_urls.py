"""Web (session-auth HTMX) URL routes for apps.academic — mounted under /web/academic/.

These are page/fragment routes (HTML responses), distinct from the JSON API
routes in urls.py (mounted under /api/v1/). Both share the same services and
permission keys.
"""
from django.urls import path

from apps.academic.views import (
    PermissionSlipConsoleCreateView,
    PermissionSlipConsolePageView,
    PermissionSlipConsoleRosterView,
    PermissionSlipConsoleTallyView,
)

urlpatterns = [
    path('permission-slips/', PermissionSlipConsolePageView.as_view(), name='permission-slip-console-page'),
    path('permission-slips/create/', PermissionSlipConsoleCreateView.as_view(), name='permission-slip-console-create'),
    path('permission-slips/<int:slip_id>/', PermissionSlipConsoleTallyView.as_view(), name='permission-slip-console-tally'),
    path('permission-slips/<int:slip_id>/roster/', PermissionSlipConsoleRosterView.as_view(), name='permission-slip-console-roster'),
]

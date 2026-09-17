"""URL routing for Calendar Sync (spec/14 §6, second purpose of the
Google Workspace / Microsoft 365 integration)."""
from django.urls import path

from .views import (
    CalendarConnectStartView,
    CalendarConnectionView,
    CalendarEventListView,
    CalendarOAuthCallbackView,
)

app_name = 'calendar_sync'

# Mounted under /api/v1/ (API views) and /web/ (public OAuth callback),
# see educore/urls.py.
urlpatterns = [
    path('auth/calendar/connection/', CalendarConnectionView.as_view(), name='calendar-connection'),
    path('auth/calendar/events/', CalendarEventListView.as_view(), name='calendar-events'),
    path('auth/calendar/connect/<str:provider>/start/', CalendarConnectStartView.as_view(),
         name='calendar-connect-start'),
    path('auth/calendar/callback/', CalendarOAuthCallbackView.as_view(), name='calendar-oauth-callback'),
]

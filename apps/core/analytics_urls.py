from django.urls import path
from apps.core.views import AnalyticsEventIngestView

urlpatterns = [
    path('analytics/events/', AnalyticsEventIngestView.as_view(), name='analytics-event-ingest'),
]

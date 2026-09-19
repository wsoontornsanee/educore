from django.urls import path

from apps.reporting.views import HealthMetricsView

urlpatterns = [
    path('health-metrics/', HealthMetricsView.as_view(), name='internal-health-metrics'),
]

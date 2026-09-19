from django.urls import path

from apps.reporting.views import MeteringRosterView, MeteringStatementView

urlpatterns = [
    path('statements/', MeteringStatementView.as_view(), name='metering-statements'),
    path('statements/roster/', MeteringRosterView.as_view(), name='metering-roster'),
]

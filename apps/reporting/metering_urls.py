from django.urls import path

from apps.reporting.views import MeteringStatementView

urlpatterns = [
    path('statements/', MeteringStatementView.as_view(), name='metering-statements'),
]

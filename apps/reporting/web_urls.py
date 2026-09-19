"""Web (session-auth) URL routes for the metering console page: mounted under /web/admin/metering/."""
from django.urls import path

from .web_views import MeteringRosterPageView, MeteringStatementPageView

urlpatterns = [
    path('', MeteringStatementPageView.as_view(), name='admin-metering'),
    path('roster/', MeteringRosterPageView.as_view(), name='admin-metering-roster'),
]

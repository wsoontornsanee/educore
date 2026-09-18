"""Web (session-auth) URL routes for apps.status — mounted under /web/status/manage/."""
from django.urls import path

from . import web_views

app_name = 'status_manage'

urlpatterns = [
    path('manage/', web_views.StatusManagePageView.as_view(), name='page'),
    path('manage/components/<int:component_id>/', web_views.StatusComponentUpdateView.as_view(), name='component-update'),
    path('manage/incidents/create/', web_views.StatusIncidentCreateView.as_view(), name='incident-create'),
    path('manage/incidents/<int:incident_id>/', web_views.StatusIncidentUpdateView.as_view(), name='incident-update'),
]

"""Web (session-auth) URL routes for the Administrasi console pages owned by apps.foundation — mounted under /web/admin/."""
from django.urls import path

from .web_views import (
    AuditExportView, AuditLogView, FoundationProfileUpdateView, SchoolActiveToggleView, SchoolCreateView,
    SchoolSettingsUpdateView, SettingsView,
)

urlpatterns = [
    path('audit/', AuditLogView.as_view(), name='admin-audit'),
    path('audit/export/', AuditExportView.as_view(), name='admin-audit-export'),
    path('settings/', SettingsView.as_view(), name='admin-settings'),
    path('settings/foundation/', FoundationProfileUpdateView.as_view(), name='admin-settings-foundation'),
    path('settings/schools/new/', SchoolCreateView.as_view(), name='admin-settings-school-new'),
    path('settings/schools/<int:school_id>/active/', SchoolActiveToggleView.as_view(), name='admin-settings-school-active'),
    path('settings/schools/<int:school_id>/', SchoolSettingsUpdateView.as_view(), name='admin-settings-school'),
]

"""Web (session-auth) URL routes for the Administrasi console's staff page — mounted under /web/admin/staff/."""
from django.urls import path

from .web_admin_views import StaffDirectoryView

urlpatterns = [
    path('', StaffDirectoryView.as_view(), name='admin-staff'),
]

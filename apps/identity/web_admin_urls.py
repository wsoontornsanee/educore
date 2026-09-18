"""Web (session-auth) URL routes for the Administrasi console's staff page — mounted under /web/admin/staff/."""
from django.urls import path

from .web_admin_views import StaffCreateView, StaffDirectoryView, StaffOffboardView

urlpatterns = [
    path('', StaffDirectoryView.as_view(), name='admin-staff'),
    path('new/', StaffCreateView.as_view(), name='admin-staff-create'),
    path('<int:staff_id>/offboard/', StaffOffboardView.as_view(), name='admin-staff-offboard'),
]

"""Web (session-auth) URL routes for the Administrasi console's staff page — mounted under /web/admin/staff/."""
from django.urls import path

from .web_admin_views import (
    StaffCreateView, StaffDirectoryView, StaffOffboardView, StaffRoleGrantView, StaffRoleRevokeView, StaffRolesView,
)

urlpatterns = [
    path('', StaffDirectoryView.as_view(), name='admin-staff'),
    path('new/', StaffCreateView.as_view(), name='admin-staff-create'),
    path('<int:staff_id>/offboard/', StaffOffboardView.as_view(), name='admin-staff-offboard'),
    path('<int:staff_id>/roles/', StaffRolesView.as_view(), name='admin-staff-roles'),
    path('<int:staff_id>/roles/grant/', StaffRoleGrantView.as_view(), name='admin-staff-role-grant'),
    path('<int:staff_id>/roles/<int:assignment_id>/revoke/', StaffRoleRevokeView.as_view(), name='admin-staff-role-revoke'),
]

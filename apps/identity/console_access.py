"""Shared access gate for the school-side web console pages (HTMX/session-auth).

Every Operasional console page (gate attendance, canteen & wallet, exam mode,
permission slips) needs the same three checks the JSON API does not do for a
browser page on its own:

1. the RBAC permission key (HasRequiredPermission, fail-closed IAM-010);
2. a linked Staff profile — ROLE_PARENT also holds attendance.read /
   grades.read / wallet.topup.read, so a guardian must never reach a
   school-side console even though the permission key alone would let them in;
3. school scoping — a school-scoped role sees only the schools it is assigned
   to, a foundation-scoped role sees every school in the foundation.
"""
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound

from educore.middleware.tenancy import get_current_foundation_id

from .models import RoleAssignment, School, Staff
from .permissions import HasRequiredPermission
from .rbac import SCOPE_SCHOOL, get_user_permissions


def permitted_schools(user, foundation_id, permission_key):
    """Schools of the foundation in which `user` holds `permission_key`."""
    schools = School.all_tenants.filter(
        foundation_id=foundation_id, deleted_at__isnull=True, is_active=True,
    ).order_by('name')
    if permission_key in get_user_permissions(user, foundation_id, school_id=None):
        return schools
    assigned_ids = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    allowed_ids = [
        school_id for school_id in set(assigned_ids)
        if permission_key in get_user_permissions(user, foundation_id, school_id=school_id)
    ]
    return schools.filter(id__in=allowed_ids)


class StaffConsoleMixin:
    """Gating for school-side console views: permission key + Staff profile + school scope.

    The concrete view declares `get_required_permission()`; `console_context`
    then resolves everything else and 404s (never 403s) for a missing Staff
    profile or a school the user cannot see, so a guardian or a user of
    another school learns nothing about what exists.
    """

    permission_classes = [HasRequiredPermission]

    def _resolve_staff(self, request):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return None
        return Staff.objects.filter(
            user=request.user, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('person', 'school').first()

    def console_context(self, request):
        """Return (foundation_id, schools, selected_school).

        `selected_school` follows `?school_id=`, defaulting to the first
        permitted school (None when the user has no permitted school yet)."""
        staff = self._resolve_staff(request)
        if staff is None:
            raise NotFound(_("Akun ini tidak terhubung ke profil staf."))

        foundation_id = get_current_foundation_id()
        schools = list(permitted_schools(request.user, foundation_id, self.get_required_permission()))

        raw_school_id = request.query_params.get('school_id')
        if not raw_school_id:
            return foundation_id, schools, (schools[0] if schools else None)
        try:
            school_id = int(raw_school_id)
        except ValueError:
            raise NotFound(_("Sekolah tidak ditemukan."))
        school = next((s for s in schools if s.id == school_id), None)
        if school is None:
            raise NotFound(_("Sekolah tidak ditemukan."))
        return foundation_id, schools, school

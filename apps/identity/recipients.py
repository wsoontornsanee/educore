"""Who to notify for school-level operational alerts."""
from typing import List

from .models import RoleAssignment, User


def get_school_admin_users(school) -> List[User]:
    """Whoever currently holds `school_admin` at the school's scope, or `foundation_admin` at the
    foundation scope. Role assignments are the source of truth, mirroring
    `apps.academic.services._get_principal_staff`, generalised to several recipients."""
    user_ids = set(RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_SCHOOL_ADMIN,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=school.id,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True))
    user_ids |= set(RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
        scope_type=RoleAssignment.SCOPE_FOUNDATION,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True))
    if not user_ids:
        return []
    return list(User.all_tenants.filter(id__in=user_ids, deleted_at__isnull=True))

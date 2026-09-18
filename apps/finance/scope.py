"""Staff-side financial data scope, shared by the JSON viewsets and the web console."""
from typing import Optional, Set

from apps.identity.models import RoleAssignment


def staff_school_scope(user, foundation_id) -> Optional[Set[int]]:
    """School ids a finance/school-admin user may see financial records for.

    Returns None when unrestricted (superuser, or foundation-wide
    foundation_admin/finance_officer); otherwise the set of school ids they
    hold a school-scoped school_admin/finance_officer assignment for (empty
    when they hold none, meaning "sees nothing").
    """
    if user.is_superuser:
        return None

    has_foundation_wide = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role__in=[RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.ROLE_FINANCE_OFFICER],
        scope_type=RoleAssignment.SCOPE_FOUNDATION,
        deleted_at__isnull=True,
    ).exists()
    if has_foundation_wide:
        return None

    return set(RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role__in=[RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.ROLE_FINANCE_OFFICER],
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        deleted_at__isnull=True,
    ).values_list('scope_id', flat=True))

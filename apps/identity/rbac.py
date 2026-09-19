"""Role-Based Access Control (RBAC) Matrix and Permission Evaluator (spec/02 §4).

Implements:
- 6 canonical roles: foundation_admin, school_admin, finance_officer, teacher, counsellor, parent
- 2 scoping types: FOUNDATION, SCHOOL
- spec/02 §4.2 baseline permission matrix
- Scope-aware permission evaluation (IAM-012)
- Fail-closed permission checking (IAM-010)
"""
from typing import Optional, Set
from django.db.models import Q
from .models import RoleAssignment, User, PlatformRoleAssignment

# Canonical Roles (spec/02 §4.2)
ROLE_FOUNDATION_ADMIN = RoleAssignment.ROLE_FOUNDATION_ADMIN
ROLE_SCHOOL_ADMIN = RoleAssignment.ROLE_SCHOOL_ADMIN
ROLE_FINANCE_OFFICER = RoleAssignment.ROLE_FINANCE_OFFICER
ROLE_TEACHER = RoleAssignment.ROLE_TEACHER
ROLE_COUNSELLOR = RoleAssignment.ROLE_COUNSELLOR
ROLE_CANTEEN_OPERATOR = RoleAssignment.ROLE_CANTEEN_OPERATOR
ROLE_CLINIC_OFFICER = RoleAssignment.ROLE_CLINIC_OFFICER
ROLE_PARENT = RoleAssignment.ROLE_PARENT

# Scope Types
SCOPE_FOUNDATION = RoleAssignment.SCOPE_FOUNDATION
SCOPE_SCHOOL = RoleAssignment.SCOPE_SCHOOL

# Permission Keys Matrix (spec/02 §4.2)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_FOUNDATION_ADMIN: {
        'school_config.read', 'school_config.write',
        'student_records.read', 'student_records.write',
        'grades.read',
        'attendance.read', 'attendance.write',
        'finance.invoice.read', 'finance.invoice.write',
        'finance.payment_intent.create',
        'finance.payment.read', 'finance.payment.write',
        'wallet.topup.read',
        'behaviour.read',
        'clinic.read', 'clinic.write',
        'library.read',
        'payroll.read', 'payroll.write',
        'hardware.read', 'hardware.write',
        'pos.manage', 'pos.collect',
        'audit_log.read',
        'reporting.read',
        'analytics.event.write',
    },
    ROLE_SCHOOL_ADMIN: {
        'pickup.override',  # ATT-018: release a student to an unauthorised person, with a written reason
        'school_config.read', 'school_config.write',
        'student_records.read', 'student_records.write',
        'grades.read', 'grades.write',
        'attendance.read', 'attendance.write',
        'finance.invoice.read',
        'finance.payment.read',
        'wallet.topup.read',
        'behaviour.read', 'behaviour.write',
        'clinic.read', 'clinic.write',
        'library.read', 'library.write',
        'payroll.read',
        'hardware.read', 'hardware.write',
        'pos.manage', 'pos.collect',
        'audit_log.read',
        'reporting.read',
        'analytics.event.write',
    },
    ROLE_FINANCE_OFFICER: {
        'student_records.read',
        'finance.invoice.read', 'finance.invoice.write',
        'finance.payment_intent.create',
        'finance.payment.read', 'finance.payment.write',
        'wallet.topup.read', 'wallet.topup.write',
        'behaviour.read',
        'payroll.read', 'payroll.write',
        'audit_log.read',
        'reporting.read',
        'analytics.event.write',
    },
    ROLE_TEACHER: {
        'student_records.read',
        'grades.read', 'grades.write',
        'attendance.read', 'attendance.write',
        'behaviour.read', 'behaviour.write',
        'analytics.event.write',
    },
    ROLE_COUNSELLOR: {
        'student_records.read',
        'grades.read',
        'attendance.read',
        'behaviour.read', 'behaviour.write',
        'clinic.read',
        'analytics.event.write',
    },
    ROLE_CANTEEN_OPERATOR: {
        'student_records.read',
        'wallet.topup.read',
        'wallet.topup.write',
        'pos.manage', 'pos.collect',
        'analytics.event.write',
    },
    ROLE_CLINIC_OFFICER: {
        'student_records.read',
        'clinic.read', 'clinic.write',
        'analytics.event.write',
    },
    ROLE_PARENT: {
        'pickup.authorize',  # ATT-015: a guardian authorises a named person to collect their child
        'student_records.read',
        'grades.read',
        'attendance.read',
        'finance.invoice.read',
        # PAR-006: guardians may only self-serve a payment intent for their own
        # outstanding invoices. This deliberately does NOT reuse
        # 'finance.invoice.write', which gates fee/discount CRUD, invoice
        # cancel/write-off, cash & manual payment recording and fiscal period
        # close — none of which a guardian may ever reach.
        'finance.payment_intent.create',
        'wallet.topup.read', 'wallet.topup.write',
        'behaviour.read',
        'clinic.read',
        'library.read',
        'analytics.event.write',
    },
}

def assign_role(
    user: User,
    role: str,
    scope_type: str,
    scope_id: int,
    foundation_id: Optional[int] = None,
    created_by: Optional[str] = None
) -> RoleAssignment:
    """Idempotently assign a role to a user within a foundation or school scope."""
    if foundation_id is None:
        foundation_id = user.foundation_id

    # with_deleted(): all_tenants hides soft-deleted rows, so without it a
    # previously revoked assignment is never found and the INSERT collides with
    # the unique_user_role_scope constraint (which does not include deleted_at).
    assignment, _ = RoleAssignment.all_tenants.with_deleted().update_or_create(
        foundation_id=foundation_id,
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id,
        defaults={
            'created_by': created_by,
            'deleted_at': None,  # un-delete if soft-deleted previously
        }
    )
    return assignment

def revoke_role(
    user: User,
    role: str,
    scope_type: str,
    scope_id: int,
    foundation_id: Optional[int] = None
) -> bool:
    """Soft-delete a role assignment (IAM-021)."""
    if foundation_id is None:
        foundation_id = user.foundation_id

    assignments = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id,
        deleted_at__isnull=True,
    )
    if not assignments.exists():
        return False

    for assignment in assignments:
        assignment.delete()
    return True

def get_user_role_assignments(
    user: User,
    foundation_id: int,
    school_id: Optional[int] = None
) -> list[RoleAssignment]:
    """Retrieve active role assignments applicable to the given foundation and school context.
    
    Rules (IAM-012):
    - A role with scope_type=FOUNDATION and scope_id=foundation_id applies across the entire foundation.
    - A role with scope_type=SCHOOL applies ONLY if school_id matches scope_id.
    - If school_id is None, only FOUNDATION-scoped roles apply.
    """
    if not user.is_authenticated or not user.is_active or user.is_locked:
        return []

    # Unscoped query filtered explicitly by foundation_id and not deleted
    q_foundation = Q(scope_type=SCOPE_FOUNDATION, scope_id=foundation_id)

    if school_id is not None:
        q_school = Q(scope_type=SCOPE_SCHOOL, scope_id=school_id)
        query = q_foundation | q_school
    else:
        query = q_foundation

    return list(
        RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id,
            user=user,
            deleted_at__isnull=True,
        ).filter(query)
    )

def get_user_permissions(
    user: User,
    foundation_id: int,
    school_id: Optional[int] = None
) -> Set[str]:
    """Calculate the cumulative set of permission keys for a user in the given context."""
    if not user.is_authenticated or not user.is_active or user.is_locked:
        return set()

    if user.is_superuser:
        # Superuser has all known permissions
        all_perms = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms.update(perms)
        return all_perms

    assignments = get_user_role_assignments(user, foundation_id, school_id)
    permissions = set()
    for assignment in assignments:
        role_perms = ROLE_PERMISSIONS.get(assignment.role, set())
        permissions.update(role_perms)

    return permissions

def has_permission(
    user: User,
    permission_key: str,
    foundation_id: int,
    school_id: Optional[int] = None
) -> bool:
    """Evaluate whether the user holds a specific permission key in the specified context (IAM-010, IAM-012)."""
    if not user.is_authenticated or not user.is_active or user.is_locked:
        return False

    user_perms = get_user_permissions(user, foundation_id, school_id)
    return permission_key in user_perms


def has_permission_in_any_scope(user: User, permission_key: str, foundation_id: int) -> bool:
    """True if the user holds `permission_key` foundation-wide, or at any school they are individually assigned to.

    For pages that aren't tied to one school (console landing/list pages):
    school-scoped staff hold their permissions at school scope only, so a
    foundation-scope-only check would wrongly lock them out.
    """
    if has_permission(user, permission_key, foundation_id, school_id=None):
        return True
    assigned_schools = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    return any(
        has_permission(user, permission_key, foundation_id, school_id=school_id)
        for school_id in assigned_schools
    )


def is_foundation_admin(user: User, foundation_id: int) -> bool:
    """Evaluate whether the user has active Foundation Admin authority (or is superuser).

    Required for approving threshold-gated actions like discounts, write-offs, and refunds
    (spec/03 §3 FND-007, FND-008, spec/06 §6 FIN-031, spec/06 §7 FIN-032).
    """
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return False

    if user.is_superuser:
        return True

    return RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role=ROLE_FOUNDATION_ADMIN,
        scope_type=SCOPE_FOUNDATION,
        scope_id=foundation_id,
        deleted_at__isnull=True,
    ).exists()


# Platform-wide (non-tenant) roles and permissions — see
# docs/superpowers/specs/2026-09-18-service-status-page-design.md §4.
# Deliberately separate from ROLE_PERMISSIONS/RoleAssignment: a platform
# permission never requires (or implies) a foundation_id.
PLATFORM_ROLE_OPERATOR = PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR

PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    PLATFORM_ROLE_OPERATOR: {'status.write'},
}

# All platform-grantable permission key strings, flattened for a cheap O(1)
# membership check — lets HasRequiredPermission skip the
# PlatformRoleAssignment DB query entirely for the overwhelming majority of
# requests, whose required_permission is a tenant-scoped key that could
# never be granted by a platform role.
PLATFORM_PERMISSION_KEYS: frozenset[str] = frozenset().union(*PLATFORM_ROLE_PERMISSIONS.values())


def get_platform_permissions(user) -> Set[str]:
    """Calculate the cumulative set of platform-wide permission keys for a user."""
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return set()

    if user.is_superuser:
        all_perms: Set[str] = set()
        for perms in PLATFORM_ROLE_PERMISSIONS.values():
            all_perms.update(perms)
        return all_perms

    assignments = PlatformRoleAssignment.objects.filter(user=user)
    permissions: Set[str] = set()
    for assignment in assignments:
        permissions.update(PLATFORM_ROLE_PERMISSIONS.get(assignment.role, set()))
    return permissions


def has_platform_permission(user, permission_key: str) -> bool:
    """True if user holds permission_key via a platform-wide role (no tenant context needed).

    Fast pre-check: `permission_key` values that no platform role could ever
    grant (i.e. every tenant-scoped permission key — the overwhelming
    majority of checks in the whole monolith) are rejected before touching
    the database at all, via the PLATFORM_PERMISSION_KEYS set already
    computed above. This makes has_platform_permission itself safe to call
    from any call site (not just HasRequiredPermission, which has its own
    equivalent short-circuit) without triggering a PlatformRoleAssignment
    query per request.
    """
    if permission_key not in PLATFORM_PERMISSION_KEYS:
        return False
    return permission_key in get_platform_permissions(user)

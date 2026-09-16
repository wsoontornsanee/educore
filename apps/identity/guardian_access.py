"""Guardian and Parent Access Authorization Service (spec/02 §4 IAM-014, spec/08 PAR-010, PAR-017).

Under Indonesian UU PDP No. 27/2022 and EduCore architectural specifications:
- IAM-014: Parents/guardians must only access data for students they have an active GuardianLink with.
- PAR-017: Guardians without financial_responsible=True must not see invoices or amounts owed.
- IAM-009: Users with staff roles in one school and parent roles in another retain staff access
  only in their assigned staff school, and parent-filtered access in their non-staff schools.
"""
from typing import Optional, Set
from .models import GuardianLink, RoleAssignment, Student, User

STAFF_ROLES = {
    RoleAssignment.ROLE_FOUNDATION_ADMIN,
    RoleAssignment.ROLE_SCHOOL_ADMIN,
    RoleAssignment.ROLE_FINANCE_OFFICER,
    RoleAssignment.ROLE_TEACHER,
    RoleAssignment.ROLE_COUNSELLOR,
}


def is_staff_user(user: User, foundation_id: int, school_id: Optional[int] = None) -> bool:
    """Determine if a user holds staff/administrative roles within the given foundation or school context.
    
    If school_id is provided, checks if the user has foundation-wide admin privileges OR
    a staff role specifically scoped to that school.
    If school_id is None, checks if the user has ANY staff/admin role across the foundation.
    """
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return False

    if user.is_superuser:
        return True

    # Check foundation-wide staff
    fnd_staff = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role__in=STAFF_ROLES,
        scope_type=RoleAssignment.SCOPE_FOUNDATION,
        deleted_at__isnull=True,
    ).exists()
    if fnd_staff:
        return True

    if school_id is not None:
        return RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id,
            user=user,
            role__in=STAFF_ROLES,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=school_id,
            deleted_at__isnull=True,
        ).exists()

    # Any staff role in any school or foundation
    return RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        role__in=STAFF_ROLES,
        deleted_at__isnull=True,
    ).exists()


def get_guardian_student_ids(user: User, foundation_id: int, financial_only: bool = False) -> Set[int]:
    """Retrieve the set of student IDs linked to this user via active GuardianLink records (IAM-014).
    
    If financial_only=True, restricts strictly to links where financial_responsible=True (PAR-017).
    """
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return set()

    qs = GuardianLink.all_tenants.filter(
        foundation_id=foundation_id,
        guardian__user=user,
        guardian__deleted_at__isnull=True,
        deleted_at__isnull=True,
        student__deleted_at__isnull=True,
    )
    if financial_only:
        qs = qs.filter(financial_responsible=True)

    return set(qs.values_list('student_id', flat=True))


def can_guardian_access_student(
    user: User,
    student_id: int,
    foundation_id: int,
    financial_only: bool = False,
) -> bool:
    """Verify whether a user is authorized to access a specific student's record or domain data.
    
    Staff and Foundation Admins for the student's school are granted access.
    Non-staff parents are strictly restricted to students linked via active GuardianLink (IAM-014).
    If financial_only=True, requires financial_responsible=True (PAR-017).
    """
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return False

    if user.is_superuser:
        return True

    # Resolve student's school
    student = Student.objects.filter(
        id=student_id,
        foundation_id=foundation_id,
        deleted_at__isnull=True,
    ).first()
    if not student:
        return False

    # If the user is staff for this student's school (or foundation admin), allow access
    if is_staff_user(user, foundation_id, school_id=student.school_id):
        return True

    # Otherwise, user must be an active linked guardian for this student
    linked_student_ids = get_guardian_student_ids(user, foundation_id, financial_only=financial_only)
    return student.id in linked_student_ids

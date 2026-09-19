"""Per-teacher class scoping for the Akademik console (Notion: "Akademik
console: per-teacher class scoping"; spec: "Follow-on: per-teacher class
scoping" in docs/superpowers/specs/2026-09-19-web-console-akademik-design.md).

Policy: in a school where a user's only staff role is `teacher`, they see just
the classes they teach (ClassSubject.teacher) or are homeroom teacher of.
Every other staff role (admins, counsellor, finance, canteen, clinic) keeps
foundation-wide reads, and so does a superuser. A teacher who is also, say, a
school admin in that school is unrestricted there. The `parent` role is not a
staff role and never lifts a restriction.

Restriction is decided per school, so a user who teaches in school A and admins
school B is restricted in A only.
"""
from django.db.models import Q

from apps.academic.models import ClassEnrollment, ClassGroup, ClassSubject
from apps.identity.guardian_access import get_guardian_student_ids
from apps.identity.models import RoleAssignment, School, Staff

_UNRESTRICTING_ROLES = {
    RoleAssignment.ROLE_FOUNDATION_ADMIN,
    RoleAssignment.ROLE_SCHOOL_ADMIN,
    RoleAssignment.ROLE_COUNSELLOR,
    RoleAssignment.ROLE_FINANCE_OFFICER,
    RoleAssignment.ROLE_CANTEEN_OPERATOR,
    RoleAssignment.ROLE_CLINIC_OFFICER,
}


class ClassScope:
    """What one user may see of the foundation's classes. Costs a fixed handful
    of queries at construction, none afterwards."""

    def __init__(self, user, foundation_id):
        self.foundation_id = foundation_id
        self.restricted_school_ids = set()
        self.own_class_ids = set()
        self.staff_ids = set()
        self.guardian_student_ids = set()
        if getattr(user, 'is_superuser', False):
            return

        assignments = list(RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user=user, deleted_at__isnull=True,
        ).values_list('role', 'scope_type', 'scope_id'))
        if not any(role == RoleAssignment.ROLE_TEACHER for role, _t, _s in assignments):
            return

        for school_id in School.all_tenants.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).values_list('id', flat=True):
            roles = {
                role for role, scope_type, scope_id in assignments
                if scope_type == RoleAssignment.SCOPE_FOUNDATION
                or (scope_type == RoleAssignment.SCOPE_SCHOOL and scope_id == school_id)
            }
            if RoleAssignment.ROLE_TEACHER in roles and not roles & _UNRESTRICTING_ROLES:
                self.restricted_school_ids.add(school_id)

        if self.restricted_school_ids:
            self.staff_ids = set(Staff.all_tenants.filter(
                foundation_id=foundation_id, user=user, deleted_at__isnull=True,
            ).values_list('id', flat=True))
            self.guardian_student_ids = get_guardian_student_ids(user, foundation_id)
            self.own_class_ids = set(ClassSubject.all_tenants.filter(
                foundation_id=foundation_id, teacher_id__in=self.staff_ids, deleted_at__isnull=True,
            ).values_list('class_group_id', flat=True)) | set(ClassGroup.all_tenants.filter(
                foundation_id=foundation_id, homeroom_teacher_id__in=self.staff_ids, deleted_at__isnull=True,
            ).values_list('id', flat=True))

    @property
    def is_restricted(self):
        return bool(self.restricted_school_ids)

    def q(self, class_field, school_field):
        """Q limiting a queryset to the visible classes, given the lookup paths
        of its class group id and that class group's school id."""
        if not self.restricted_school_ids:
            return Q()
        return ~Q(**{f'{school_field}__in': self.restricted_school_ids}) | Q(
            **{f'{class_field}__in': self.own_class_ids},
        )

    def student_q(self, student_field, school_field):
        """Q limiting a queryset of student-keyed rows to the visible students:
        those enrolled in one of the user's classes, plus the user's own children
        as a guardian. `student_field` is the lookup of the row's student id."""
        if not self.restricted_school_ids:
            return Q()
        own_students = ClassEnrollment.all_tenants.filter(
            foundation_id=self.foundation_id, class_group_id__in=self.own_class_ids,
            is_active=True, deleted_at__isnull=True,
        ).values('student_id')
        return (
            ~Q(**{f'{school_field}__in': self.restricted_school_ids})
            | Q(**{f'{student_field}__in': own_students})
            | Q(**{f'{student_field}__in': self.guardian_student_ids})
        )

    def class_groups(self, queryset):
        return queryset.filter(self.q('id', 'school_id'))

    def allows(self, class_group):
        return (
            class_group.school_id not in self.restricted_school_ids or class_group.id in self.own_class_ids
        )


def can_view_student_academics(user, student_id, foundation_id, scope=None):
    """Guardian/staff access to a student's academic data (grades, homework,
    report cards, timetable...), with the teacher class scope applied.

    `can_guardian_access_student` lets any staff member of the student's school
    read the student. On top of it, a restricted teacher may read only students
    enrolled in one of their classes, and a linked guardian (a teacher can also
    be a parent) always keeps access to their own child. Kept out of
    can_guardian_access_student itself because that check also guards finance,
    clinic and wallet data, which this class scope does not govern."""
    from apps.identity.guardian_access import can_guardian_access_student

    if not can_guardian_access_student(user, student_id, foundation_id):
        return False
    scope = scope or ClassScope(user, foundation_id)
    if not scope.is_restricted or student_id in scope.guardian_student_ids:
        return True
    return any(
        scope.allows(class_group) for class_group in ClassGroup.all_tenants.filter(
            id__in=ClassEnrollment.all_tenants.filter(
                foundation_id=foundation_id, student_id=student_id, is_active=True, deleted_at__isnull=True,
            ).values('class_group_id'),
            foundation_id=foundation_id,
        )
    )

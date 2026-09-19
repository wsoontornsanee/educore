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

from apps.academic.models import ClassGroup, ClassSubject
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
        self.restricted_school_ids = set()
        self.own_class_ids = set()
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
            staff_ids = list(Staff.all_tenants.filter(
                foundation_id=foundation_id, user=user, deleted_at__isnull=True,
            ).values_list('id', flat=True))
            self.own_class_ids = set(ClassSubject.all_tenants.filter(
                foundation_id=foundation_id, teacher_id__in=staff_ids, deleted_at__isnull=True,
            ).values_list('class_group_id', flat=True)) | set(ClassGroup.all_tenants.filter(
                foundation_id=foundation_id, homeroom_teacher_id__in=staff_ids, deleted_at__isnull=True,
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

    def class_groups(self, queryset):
        return queryset.filter(self.q('id', 'school_id'))

    def allows(self, class_group):
        return (
            class_group.school_id not in self.restricted_school_ids or class_group.id in self.own_class_ids
        )

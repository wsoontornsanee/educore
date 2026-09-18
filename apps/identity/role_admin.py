"""Granting and revoking staff roles (RoleAssignment) with privilege-escalation rules.

The most privilege-sensitive write in the console, so every rule lives here,
in one place, and every entry point (today the Administrasi web console) goes
through `grant_role` / `revoke_role_assignment`. Authorization is enforced
inside these functions — a caller that forgets to pre-check cannot bypass it.

Policy (actor = whoever performs the change, target = the staff member):

1. Never your own roles — grant or revoke.
2. The target must be an active staff member of the foundation, inside the
   actor's `school_config.write` ceiling (a school administrator's ceiling is
   their own schools; foundation-level staff need a foundation-wide grant).
3. Scope: a foundation-scope role needs a foundation-wide grant; a school-scope
   role needs that school inside the ceiling.
4. No escalation: unless the actor is a foundation administrator, every
   permission the role carries must be held by the actor at that scope
   (least privilege: a school administrator cannot make anyone a finance
   officer, because they hold none of the finance write permissions).
   `foundation_admin` is only ever grantable by a foundation administrator.
   The same rule applies to revoking — you cannot remove a role you could not
   have granted.
5. A foundation is never left without an active foundation administrator.
6. `parent` is a guardian role, attached through guardian links, not staff
   management, so it is not grantable here.

Permissions are evaluated live from RoleAssignment on every request (no
cache), so a change takes effect on the user's next request; there is nothing
to invalidate.
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.services import audit

from .console_access import accessible_school_ids
from .models import RoleAssignment, School, Staff, User
from .rbac import (
    ROLE_FOUNDATION_ADMIN, ROLE_PARENT, ROLE_PERMISSIONS, SCOPE_FOUNDATION, SCOPE_SCHOOL,
    assign_role, get_user_permissions, is_foundation_admin, revoke_role,
)

WRITE_PERMISSION = 'school_config.write'
GRANTABLE_ROLES = [role for role, _label in RoleAssignment.ROLE_CHOICES if role != ROLE_PARENT]


class RoleChangeError(ValidationError):
    """A refused role change; the message is safe to show the actor."""


def _actor_permissions_at(actor, foundation_id, scope_type, scope_id):
    school_id = scope_id if scope_type == SCOPE_SCHOOL else None
    return get_user_permissions(actor, foundation_id, school_id=school_id)


def _staff_for_user(foundation_id, user_id):
    return Staff.all_tenants.filter(
        foundation_id=foundation_id, user_id=user_id, deleted_at__isnull=True,
    ).exclude(status=Staff.STATUS_OFFBOARDED).select_related('person').first()


def _check_scope_and_escalation(actor, foundation_id, role, scope_type, scope_id, ceiling):
    if role not in GRANTABLE_ROLES:
        raise RoleChangeError(_("Peran ini tidak dapat dikelola dari konsol."))
    if scope_type == SCOPE_FOUNDATION:
        if scope_id != foundation_id:
            raise RoleChangeError(_("Cakupan tidak valid."))
        if ceiling is not None:
            raise RoleChangeError(_("Hanya administrator yayasan yang dapat mengelola peran tingkat yayasan."))
    elif scope_type == SCOPE_SCHOOL:
        if ceiling is not None and scope_id not in ceiling:
            raise RoleChangeError(_("Anda tidak berwenang mengelola peran di sekolah ini."))
        if not School.all_tenants.filter(
            id=scope_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).exists():
            raise RoleChangeError(_("Sekolah tidak ditemukan."))
    else:
        raise RoleChangeError(_("Cakupan tidak valid."))

    if role == ROLE_FOUNDATION_ADMIN and not is_foundation_admin(actor, foundation_id):
        raise RoleChangeError(_("Hanya administrator yayasan yang dapat mengelola peran administrator yayasan."))
    # Foundation administrators sit at the top of the hierarchy and may grant any
    # grantable role (their own permission set is deliberately not a superset of
    # e.g. finance_officer's wallet/payroll write keys). Everyone else is bound by
    # least privilege: every permission the role carries must be one they hold.
    missing = ROLE_PERMISSIONS.get(role, set()) - _actor_permissions_at(actor, foundation_id, scope_type, scope_id)
    if missing and not is_foundation_admin(actor, foundation_id):
        raise RoleChangeError(_("Anda tidak dapat mengelola peran yang memiliki hak akses melebihi hak akses Anda."))


def _check_target(actor, foundation_id, target_user_id, ceiling):
    if target_user_id == actor.id:
        raise RoleChangeError(_("Anda tidak dapat mengubah peran Anda sendiri."))
    staff = _staff_for_user(foundation_id, target_user_id)
    if staff is None:
        raise RoleChangeError(_("Staf tidak ditemukan atau sudah di-offboard."))
    if ceiling is not None and staff.school_id not in ceiling:
        raise RoleChangeError(_("Staf ini berada di luar sekolah yang Anda kelola."))
    return staff


def grant_role(*, foundation_id, actor, target_user_id, role, scope_type, scope_id, ip_address=None):
    """Grant `role` at (scope_type, scope_id). Returns (assignment, created);
    granting a role the user already holds is a no-op and writes no audit."""
    ceiling = accessible_school_ids(actor, foundation_id, WRITE_PERMISSION)
    _check_target(actor, foundation_id, target_user_id, ceiling)
    _check_scope_and_escalation(actor, foundation_id, role, scope_type, scope_id, ceiling)

    if RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user_id=target_user_id, role=role,
        scope_type=scope_type, scope_id=scope_id, deleted_at__isnull=True,
    ).exists():
        return None, False

    target = User.all_tenants.get(id=target_user_id, foundation_id=foundation_id)
    assignment = assign_role(
        user=target, role=role, scope_type=scope_type, scope_id=scope_id,
        foundation_id=foundation_id, created_by=str(actor.id),
    )
    audit(
        action='identity.role.granted', entity_type='RoleAssignment', entity_id=str(assignment.id),
        actor_id=str(actor.id), foundation_id=foundation_id,
        school_id=scope_id if scope_type == SCOPE_SCHOOL else None, ip_address=ip_address,
        diff={'user_id': target_user_id, 'role': role, 'scope_type': scope_type, 'scope_id': scope_id},
    )
    return assignment, True


def revoke_role_assignment(*, foundation_id, actor, assignment_id, ip_address=None):
    """Revoke one active assignment (soft delete) under the same rules as
    granting it, plus the last-foundation-administrator guard."""
    assignment = RoleAssignment.all_tenants.filter(
        id=assignment_id, foundation_id=foundation_id, deleted_at__isnull=True,
    ).first()
    if assignment is None:
        raise RoleChangeError(_("Penugasan peran tidak ditemukan."))

    ceiling = accessible_school_ids(actor, foundation_id, WRITE_PERMISSION)
    _check_target(actor, foundation_id, assignment.user_id, ceiling)
    _check_scope_and_escalation(
        actor, foundation_id, assignment.role, assignment.scope_type, assignment.scope_id, ceiling,
    )

    if assignment.role == ROLE_FOUNDATION_ADMIN and assignment.scope_type == SCOPE_FOUNDATION:
        remaining = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            deleted_at__isnull=True, user__is_active=True,
        ).exclude(id=assignment.id).count()
        if remaining == 0:
            raise RoleChangeError(_("Yayasan harus memiliki setidaknya satu administrator yayasan aktif."))

    revoke_role(
        user=assignment.user, role=assignment.role, scope_type=assignment.scope_type,
        scope_id=assignment.scope_id, foundation_id=foundation_id,
    )
    audit(
        action='identity.role.revoked', entity_type='RoleAssignment', entity_id=str(assignment.id),
        actor_id=str(actor.id), foundation_id=foundation_id,
        school_id=assignment.scope_id if assignment.scope_type == SCOPE_SCHOOL else None, ip_address=ip_address,
        diff={
            'user_id': assignment.user_id, 'role': assignment.role,
            'scope_type': assignment.scope_type, 'scope_id': assignment.scope_id,
        },
    )


def revoke_blocker(*, foundation_id, actor, assignment):
    """Why `actor` cannot revoke `assignment` (a message), or None if they can.
    For the UI to hide buttons; revoke_role_assignment re-checks regardless."""
    ceiling = accessible_school_ids(actor, foundation_id, WRITE_PERMISSION)
    try:
        _check_target(actor, foundation_id, assignment.user_id, ceiling)
        _check_scope_and_escalation(
            actor, foundation_id, assignment.role, assignment.scope_type, assignment.scope_id, ceiling,
        )
    except RoleChangeError as exc:
        return exc.messages[0]
    return None

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
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, PermissionDenied

from educore.middleware.tenancy import get_current_foundation_id

from .models import RoleAssignment, School, Staff
from .nav import has_staff_access
from .permissions import HasRequiredPermission
from .rbac import SCOPE_SCHOOL, get_user_permissions, has_permission_in_any_scope, is_foundation_admin


def redirect_denied_to_home(request):
    """A signed-in user reached a console page they may not use: send them home
    with a message instead of a dead end (raw 403 / blank redirect)."""
    messages.error(request, _("Anda tidak memiliki akses ke halaman tersebut."))
    return redirect('web-console-home')


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

    def handle_exception(self, exc):
        # These are HTML pages, so DRF's default JSON 403 body would land in the
        # browser as raw text. A missing Staff profile or unseen school stays a 404.
        if isinstance(exc, (PermissionDenied, DjangoPermissionDenied)) and self.request.user.is_authenticated:
            return redirect_denied_to_home(self.request)
        return super().handle_exception(exc)

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


def accessible_school_ids(user, foundation_id, permission_key):
    """School ids the user may act on for `permission_key`, for pages that
    also list foundation-wide rows and inactive schools (unlike
    permitted_schools, which is active-schools-only).

    Returns None when the permission is held at foundation scope (every
    school in the foundation, including rows that belong to no school);
    otherwise the set of school ids where a school-scoped assignment grants
    it (possibly empty)."""
    if permission_key in get_user_permissions(user, foundation_id, school_id=None):
        return None
    assigned = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    return {
        school_id for school_id in assigned
        if permission_key in get_user_permissions(user, foundation_id, school_id=school_id)
    }


def accessible_school_ids_for_all(user, foundation_id, *permission_keys):
    """School ids where `user` holds EVERY one of `permission_keys`; None means every school of the foundation.

    For surfaces that combine two gates, e.g. a metering roster is metering data (`reporting.read`) that
    lists students (`student_records.read`): the caller must hold both, and only for the same school."""
    allowed = None
    for permission_key in permission_keys:
        ceiling = accessible_school_ids(user, foundation_id, permission_key)
        if ceiling is None:
            continue
        allowed = set(ceiling) if allowed is None else allowed & set(ceiling)
    return allowed


def can_manage_staff(actor, staff, ceiling, assignments):
    """May `actor` offboard `staff`, given `ceiling` (accessible_school_ids
    for school_config.write: None = foundation-wide) and the target's active
    RoleAssignment rows? The one rule shared by the web console and the JSON
    StaffViewSet.

    Yourself is never manageable (a self-offboard would revoke your own
    sessions and roles). Whether the target is still active is the caller's
    concern (the API answers a repeat offboard with a 400 from the service).
    A school-scoped manager may
    only manage staff whose entire access lives inside their own schools: a
    foundation-scope role, a school outside the ceiling, or a superuser flag
    on the target means they hold authority the manager does not, and
    offboarding them would revoke it — privilege escalation in reverse."""
    if staff.user_id == actor.id:
        return False
    if ceiling is None:
        return True
    if staff.school_id not in ceiling or staff.user.is_superuser:
        return False
    return all(
        a.scope_type == RoleAssignment.SCOPE_SCHOOL and a.scope_id in ceiling for a in assignments
    )


class ConsolePermissionMixin(LoginRequiredMixin):
    """Login + RBAC gate for the Administrasi console pages (plain Django
    views, not DRF — unlike StaffConsoleMixin, no Staff profile is required:
    a foundation admin need not have one, and none of the Administrasi
    permission keys are held by guardian roles).

    Subclasses set `required_permission` (an RBAC key), or
    `foundation_admin_only = True` for pages whose backing API is gated by
    is_foundation_admin rather than a permission key, or
    `staff_access_only = True` for pages open to any staff user (a staff
    role or a Staff row — the task inbox: its sources each apply their own
    check, but a guardian-only account has no business on the staff console). A denied user is
    redirected to web-console-home with a message rather than shown a raw 403
    (matching StaffConsoleMixin: never leave a user at a dead end).
    `self.foundation_id` is set once the gate passes.
    """
    required_permission = None
    foundation_admin_only = False
    staff_access_only = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
            if not self.foundation_id or not self._is_allowed(request.user):
                return redirect_denied_to_home(request)
        return super().dispatch(request, *args, **kwargs)

    def _is_allowed(self, user):
        if self.foundation_admin_only:
            return is_foundation_admin(user, self.foundation_id)
        if self.staff_access_only:
            return has_staff_access(user, self.foundation_id)
        return has_permission_in_any_scope(user, self.required_permission, self.foundation_id)


def paginate_queryset(request, queryset, per_page=25):
    """Page-number pagination for console list pages. Returns
    (page_obj, query_string) where query_string is the current GET filter
    set minus `page`, ready to append to prev/next links."""
    from django.core.paginator import Paginator

    page_obj = Paginator(queryset, per_page).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page_obj, params.urlencode()

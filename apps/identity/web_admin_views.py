"""Administrasi console: Staf & jabatan (staff directory with role assignments).

Directory over apps.identity.Staff + RoleAssignment, mounted under
/web/admin/staff/. Reading is gated by school_config.read — the same key the
JSON StaffViewSet uses for list/retrieve — and narrowed to the schools the
viewer is actually assigned to (the JSON viewset is foundation-wide).

Creating and offboarding staff are gated by school_config.write and go through
the same services as the JSON StaffViewSet (create_staff_member,
offboard_staff), but — unlike the JSON viewset — are additionally ceilinged to
the schools the actor may write to: a school administrator can neither create
staff into, nor offboard staff of, another school. Role-assignment editing is
not offered here (it needs its own privilege-escalation design).
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.generic import FormView, TemplateView

from .console_access import ConsolePermissionMixin, accessible_school_ids, can_manage_staff, paginate_queryset
from .models import RoleAssignment, School, Staff
from .services import create_staff_member, offboard_staff
from .web_admin_forms import StaffCreateForm, StaffOffboardForm

WRITE_PERMISSION = 'school_config.write'


def _writable_schools(user, foundation_id):
    """(schools the user may write staff into, whether school-less/foundation-level
    staff may be created/offboarded). The ceiling is None for a foundation-wide
    grant — every school plus foundation-level staff — otherwise only the
    user's own assigned schools."""
    ceiling = accessible_school_ids(user, foundation_id, WRITE_PERMISSION)
    schools = School.all_tenants.filter(foundation_id=foundation_id, deleted_at__isnull=True, is_active=True)
    if ceiling is not None:
        schools = schools.filter(id__in=ceiling)
    return list(schools.order_by('name')), ceiling is None, ceiling


class StaffDirectoryView(ConsolePermissionMixin, TemplateView):
    template_name = 'pages/admin_staff.html'
    required_permission = 'school_config.read'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        foundation_id = self.foundation_id
        school_ids = accessible_school_ids(self.request.user, foundation_id, self.required_permission)

        schools = School.all_tenants.filter(foundation_id=foundation_id, deleted_at__isnull=True)
        staff_qs = Staff.all_tenants.filter(foundation_id=foundation_id, deleted_at__isnull=True)
        if school_ids is not None:
            schools = schools.filter(id__in=school_ids)
            staff_qs = staff_qs.filter(school_id__in=school_ids)
        schools = list(schools.order_by('name'))

        params = self.request.GET
        school_filter = params.get('school', '')
        if school_filter.isdigit():
            staff_qs = staff_qs.filter(school_id=int(school_filter))
        status_filter = params.get('status', '')
        if status_filter in dict(Staff.STATUS_CHOICES):
            staff_qs = staff_qs.filter(status=status_filter)
        else:
            status_filter = ''
        query = params.get('q', '').strip()
        if query:
            staff_qs = staff_qs.filter(Q(person__full_name__icontains=query) | Q(nip__icontains=query))

        page_obj, query_string = paginate_queryset(
            self.request, staff_qs.select_related('person', 'school', 'user').order_by('person__full_name', 'id'),
        )
        rows = list(page_obj)

        school_names = {school.id: school.name for school in schools}
        role_labels = dict(RoleAssignment.ROLE_CHOICES)
        roles_by_user = {}
        assignments_by_user = {}
        assignments = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user_id__in=[row.user_id for row in rows], deleted_at__isnull=True,
        ).order_by('role')
        for assignment in assignments:
            assignments_by_user.setdefault(assignment.user_id, []).append(assignment)
            if assignment.scope_type == RoleAssignment.SCOPE_FOUNDATION:
                scope_label = None
            else:
                scope_label = school_names.get(assignment.scope_id)
            roles_by_user.setdefault(assignment.user_id, []).append({
                'label': role_labels.get(assignment.role, assignment.role),
                'scope': scope_label,
            })
        write_ceiling = accessible_school_ids(self.request.user, foundation_id, WRITE_PERMISSION)
        for row in rows:
            row.role_list = roles_by_user.get(row.user_id, [])
            row.can_manage = row.status != Staff.STATUS_OFFBOARDED and can_manage_staff(
                self.request.user, row, write_ceiling, assignments_by_user.get(row.user_id, []),
            )

        ctx.update({
            'page_obj': page_obj,
            'query_string': query_string,
            'staff_rows': rows,
            'schools': schools,
            'status_choices': Staff.STATUS_CHOICES,
            'filters': {'school': school_filter, 'status': status_filter, 'q': query},
            'can_create': write_ceiling is None or bool(write_ceiling),
        })
        return ctx


class StaffCreateView(ConsolePermissionMixin, FormView):
    """GET/POST /web/admin/staff/new/ — add a staff member (and their login)."""
    template_name = 'pages/admin_staff_form.html'
    required_permission = WRITE_PERMISSION
    form_class = StaffCreateForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        schools, allow_no_school, _ceiling = _writable_schools(self.request.user, self.foundation_id)
        kwargs.update(schools=schools, allow_no_school=allow_no_school)
        return kwargs

    def form_valid(self, form):
        staff = create_staff_member(
            foundation_id=self.foundation_id, school=form.cleaned_data['school'], data=form.staff_data(),
            actor=self.request.user, ip_address=self.request.META.get('REMOTE_ADDR'),
        )
        messages.success(self.request, _("Staf %(name)s ditambahkan.") % {'name': staff.person.full_name})
        return redirect('admin-staff')


class StaffOffboardView(ConsolePermissionMixin, FormView):
    """GET/POST /web/admin/staff/<id>/offboard/ — confirm + run the IAM-021
    offboarding lifecycle (suspend login, revoke sessions and roles, audit)."""
    template_name = 'pages/admin_staff_offboard.html'
    required_permission = WRITE_PERMISSION
    form_class = StaffOffboardForm

    def _load(self):
        """The target Staff, looked up inside the acting user's write ceiling
        (an out-of-scope or other-tenant id is a 404, never an oracle)."""
        ceiling = accessible_school_ids(self.request.user, self.foundation_id, WRITE_PERMISSION)
        qs = Staff.all_tenants.filter(
            id=self.kwargs['staff_id'], foundation_id=self.foundation_id, deleted_at__isnull=True,
        ).select_related('person', 'school', 'user')
        if ceiling is not None:
            qs = qs.filter(school_id__in=ceiling)
        staff = qs.first()
        if staff is None:
            raise Http404
        assignments = list(RoleAssignment.all_tenants.filter(
            foundation_id=self.foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
        ))
        allowed = staff.status != Staff.STATUS_OFFBOARDED and can_manage_staff(
            self.request.user, staff, ceiling, assignments,
        )
        return staff, allowed

    def get(self, request, *args, **kwargs):
        self.staff, allowed = self._load()
        if not allowed:
            return self._refuse()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.staff, allowed = self._load()
        if not allowed:
            return self._refuse()
        return super().post(request, *args, **kwargs)

    def _refuse(self):
        messages.error(self.request, _("Staf ini tidak dapat di-offboard oleh Anda."))
        return redirect('admin-staff')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        candidates = Staff.all_tenants.filter(
            foundation_id=self.foundation_id, school_id=self.staff.school_id, status=Staff.STATUS_ACTIVE,
            deleted_at__isnull=True,
        ).exclude(id=self.staff.id).select_related('person').order_by('person__full_name')
        kwargs['reassign_candidates'] = list(candidates)
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['staff'] = self.staff
        return ctx

    def form_valid(self, form):
        try:
            offboard_staff(
                staff_id=self.staff.id, actor_id=str(self.request.user.id),
                role=getattr(self.request.user, 'role', 'school_admin'),
                reason=form.cleaned_data['reason'], resignation_date=form.cleaned_data['resignation_date'],
                reassign_to_staff_id=form.cleaned_data['reassign_to'],
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        messages.success(
            self.request, _("Staf %(name)s telah di-offboard.") % {'name': self.staff.person.full_name},
        )
        return redirect('admin-staff')

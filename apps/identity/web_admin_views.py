"""Administrasi console: Staf & jabatan (staff directory, onboarding, offboarding).

Mounted under /web/admin/staff/. The directory is gated by school_config.read —
the key the JSON StaffViewSet uses for list/retrieve — and narrowed to the
schools the viewer is actually assigned to (the JSON viewset is foundation-
wide). Creating and offboarding staff needs school_config.write and goes
through the same services as the API (create_staff, offboard_staff), with the
web console additionally enforcing the actor's school ceiling: a school-scoped
manager can only act inside their own schools, and can never offboard someone
holding authority beyond them (console_access.can_manage_staff).
Role-assignment editing is not here: it needs its own escalation rules.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from .console_access import (
    ConsolePermissionMixin, accessible_school_ids, can_manage_staff, paginate_queryset,
)
from .forms import StaffCreateForm
from .models import RoleAssignment, School, Staff
from .rbac import has_permission_in_any_scope
from .services import create_staff, offboard_staff


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
            self.request, staff_qs.select_related('person', 'school').order_by('person__full_name', 'id'),
        )
        rows = list(page_obj)

        school_names = {school.id: school.name for school in schools}
        role_labels = dict(RoleAssignment.ROLE_CHOICES)
        roles_by_user = {}
        assignments = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user_id__in=[row.user_id for row in rows], deleted_at__isnull=True,
        ).order_by('role')
        assignments_by_user = {}
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
        can_write = has_permission_in_any_scope(self.request.user, 'school_config.write', foundation_id)
        write_ceiling = accessible_school_ids(self.request.user, foundation_id, 'school_config.write')
        colleagues = {}
        if can_write:
            for staff_id, name, school_id in Staff.all_tenants.filter(
                foundation_id=foundation_id, deleted_at__isnull=True, status=Staff.STATUS_ACTIVE,
                school_id__in={row.school_id for row in rows if row.school_id},
            ).order_by('person__full_name').values_list('id', 'person__full_name', 'school_id'):
                colleagues.setdefault(school_id, []).append((staff_id, name))
        for row in rows:
            row.role_list = roles_by_user.get(row.user_id, [])
            row.can_offboard = (
                can_write and row.status != Staff.STATUS_OFFBOARDED
                and can_manage_staff(self.request.user, row, write_ceiling, assignments_by_user.get(row.user_id, []))
            )
            row.colleague_options = [(i, n) for i, n in colleagues.get(row.school_id, []) if i != row.id]

        ctx.update({
            'page_obj': page_obj,
            'query_string': query_string,
            'staff_rows': rows,
            'can_write': can_write,
            'schools': schools,
            'status_choices': Staff.STATUS_CHOICES,
            'filters': {'school': school_filter, 'status': status_filter, 'q': query},
        })
        return ctx


class _StaffWriteMixin(ConsolePermissionMixin):
    required_permission = 'school_config.write'

    def ceiling(self):
        return accessible_school_ids(self.request.user, self.foundation_id, self.required_permission)


class StaffCreateView(_StaffWriteMixin, TemplateView):
    """GET/POST /web/admin/staff/new/ — onboard a staff member into a school
    the actor may write to (or foundation-wide, for foundation-scope writers)."""
    template_name = 'pages/admin_staff_form.html'

    def _form(self, data=None):
        ceiling = self.ceiling()
        schools = School.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True, is_active=True)
        if ceiling is not None:
            schools = schools.filter(id__in=ceiling)
        return StaffCreateForm(data, school_choices=schools.order_by('name'), allow_foundation_wide=ceiling is None)

    def get_context_data(self, form=None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['form'] = form or self._form()
        return ctx

    def post(self, request):
        form = self._form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        school = None
        if form.school_id():
            # The ChoiceField already limited this to the actor's ceiling; the
            # lookup is still tenant-scoped rather than trusting a bare id.
            school = get_object_or_404(School.all_tenants, id=form.school_id(), foundation_id=self.foundation_id)
        create_staff(
            foundation_id=self.foundation_id, school=school, data=form.staff_data(),
            actor_id=str(request.user.id), role=getattr(request.user, 'role', ''),
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        messages.success(request, _("Staf %(name)s ditambahkan.") % {'name': form.cleaned_data['full_name']})
        return redirect('admin-staff')


class StaffOffboardView(_StaffWriteMixin, View):
    """POST /web/admin/staff/<id>/offboard/ — IAM-021 offboarding (suspend the
    login, revoke sessions and roles, emit the class-reassignment event).
    A target outside the actor's authority is a 404, never a 403, so a
    school-scoped manager learns nothing about staff they cannot manage."""
    http_method_names = ['post']

    def post(self, request, staff_id):
        staff = get_object_or_404(
            Staff.all_tenants.select_related('person', 'user'),
            id=staff_id, foundation_id=self.foundation_id, deleted_at__isnull=True,
        )
        assignments = RoleAssignment.all_tenants.filter(
            foundation_id=self.foundation_id, user_id=staff.user_id, deleted_at__isnull=True,
        )
        if not can_manage_staff(request.user, staff, self.ceiling(), list(assignments)):
            raise Http404

        reassign_to = None
        raw_reassign = request.POST.get('reassign_to_staff_id', '')
        if raw_reassign:
            reassign_to = Staff.all_tenants.filter(
                id=raw_reassign if raw_reassign.isdigit() else 0, foundation_id=self.foundation_id,
                school_id=staff.school_id, status=Staff.STATUS_ACTIVE, deleted_at__isnull=True,
            ).exclude(id=staff.id).first()
            if reassign_to is None:
                messages.error(request, _("Staf pengganti tidak valid."))
                return redirect('admin-staff')

        try:
            resignation_date = _parse_date(request.POST.get('resignation_date'))
            offboard_staff(
                staff_id=staff.id, actor_id=str(request.user.id), role=getattr(request.user, 'role', ''),
                reason=request.POST.get('reason', '').strip(), resignation_date=resignation_date,
                reassign_to_staff_id=reassign_to.id if reassign_to else None,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect('admin-staff')
        messages.success(request, _("Staf %(name)s di-offboard.") % {'name': staff.person.full_name})
        return redirect('admin-staff')


def _parse_date(raw):
    """A submitted YYYY-MM-DD or None (blank = today, chosen by the service)."""
    from django.utils.dateparse import parse_date

    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed is None:
        raise ValidationError(_("Tanggal pengunduran diri tidak valid."))
    return parsed

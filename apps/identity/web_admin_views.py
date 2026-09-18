"""Administrasi console: Staf & jabatan (staff directory with role assignments).

Read-only surface over apps.identity.Staff + RoleAssignment, mounted under
/web/admin/staff/. Gated by school_config.read — the same key the JSON
StaffViewSet uses for list/retrieve — and narrowed to the schools the viewer
is actually assigned to (the JSON viewset is foundation-wide). Creating and
offboarding staff stays on the API (StaffViewSet).
"""
from django.db.models import Q
from django.views.generic import TemplateView

from .console_access import ConsolePermissionMixin, accessible_school_ids, paginate_queryset
from .models import RoleAssignment, School, Staff


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
        for assignment in assignments:
            if assignment.scope_type == RoleAssignment.SCOPE_FOUNDATION:
                scope_label = None
            else:
                scope_label = school_names.get(assignment.scope_id)
            roles_by_user.setdefault(assignment.user_id, []).append({
                'label': role_labels.get(assignment.role, assignment.role),
                'scope': scope_label,
            })
        for row in rows:
            row.role_list = roles_by_user.get(row.user_id, [])

        ctx.update({
            'page_obj': page_obj,
            'query_string': query_string,
            'staff_rows': rows,
            'schools': schools,
            'status_choices': Staff.STATUS_CHOICES,
            'filters': {'school': school_filter, 'status': status_filter, 'q': query},
        })
        return ctx

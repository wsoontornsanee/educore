"""Administrasi console pages owned by apps.foundation, mounted under /web/admin/.

Session-auth HTML surfaces over the same services the JSON views use, so the
console and the API can never disagree about what a filter or a write means.
"""
import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.core.models import ExportJob
from apps.core.services import audit, create_export_job, get_export_job_status
from apps.foundation.forms import FoundationProfileForm, SchoolCreateForm, SchoolSettingsForm
from apps.foundation.services import REPORT_KEY_FOUNDATION_AUDIT, filter_foundation_audit_events
from apps.identity.console_access import ConsolePermissionMixin, accessible_school_ids, paginate_queryset
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import is_foundation_admin


AUDIT_FILTER_KEYS = ('module', 'action', 'school', 'from', 'to', 'archived')


def read_audit_filters(params):
    """The viewer's filter set from a GET/POST querystring, shared by the
    page and its export so both mean the same thing. Returns
    (filters, invalid_dates); an unparseable date is dropped and reported."""
    filters = {key: params.get(key, '').strip() for key in AUDIT_FILTER_KEYS}
    invalid_dates = [key for key in ('from', 'to') if filters[key] and not parse_date(filters[key])]
    for key in invalid_dates:
        filters[key] = ''
    return filters, invalid_dates


class AuditLogView(ConsolePermissionMixin, TemplateView):
    """Jejak audit: filterable, paginated viewer over core.AuditEvent
    (FND-010), reusing filter_foundation_audit_events. A viewer whose
    audit_log.read is school-scoped sees only events of their own schools —
    foundation-level events (school_id NULL, e.g. API key issuance) stay
    with foundation-scope holders."""
    template_name = 'pages/admin_audit.html'
    required_permission = 'audit_log.read'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        foundation_id = self.foundation_id
        school_ids = accessible_school_ids(self.request.user, foundation_id, self.required_permission)

        schools = School.all_tenants.filter(foundation_id=foundation_id, deleted_at__isnull=True)
        if school_ids is not None:
            schools = schools.filter(id__in=school_ids)
        schools = list(schools.order_by('name'))

        filters, invalid_dates = read_audit_filters(self.request.GET)

        events = filter_foundation_audit_events(
            foundation_id,
            school_id=int(filters['school']) if filters['school'].isdigit() else None,
            module=filters['module'] or None,
            action=filters['action'] or None,
            from_date=filters['from'] or None,
            to_date=filters['to'] or None,
            school_ids=school_ids,
            archived=filters['archived'] == '1',
        )

        page_obj, query_string = paginate_queryset(self.request, events, per_page=50)
        rows = list(page_obj)

        actor_ids = {int(row.actor_id) for row in rows if row.actor_id and row.actor_id.isdigit()}
        actor_names = {
            str(user.id): user.full_name
            for user in User.all_tenants.filter(foundation_id=foundation_id, id__in=actor_ids)
        }
        school_names = {school.id: school.name for school in schools}
        for row in rows:
            row.actor_name = actor_names.get(row.actor_id) or row.actor_id
            row.school_name = school_names.get(row.school_id)
            row.diff_text = json.dumps(row.diff, indent=2, ensure_ascii=False, sort_keys=True) if row.diff else ''

        ctx.update({
            'page_obj': page_obj,
            'query_string': query_string,
            'events': rows,
            'schools': schools,
            'filters': filters,
            'invalid_dates': invalid_dates,
            'recent_exports': self._recent_exports(),
        })
        return ctx

    def _recent_exports(self):
        """The viewer's own latest exports, each with a fresh signed
        download link once COMPLETED (links are 24h-expiring, so they are
        minted per render rather than stored)."""
        jobs = ExportJob.all_tenants.filter(
            foundation_id=self.foundation_id, report_key=REPORT_KEY_FOUNDATION_AUDIT,
            requested_by=str(self.request.user.id), deleted_at__isnull=True,
        ).order_by('-id')[:5]
        exports = []
        for job in jobs:
            status = get_export_job_status(job.id, foundation_id=self.foundation_id)
            exports.append({
                'id': job.id, 'created_at': job.created_at, 'status': job.status,
                'download_url': status['download_url'], 'error': status.get('error', ''),
            })
        return exports


class AuditExportView(ConsolePermissionMixin, View):
    """POST /web/admin/audit/export/?<filters> — queue a CSV export of the
    audit events the viewer's current filters select. A school-scoped viewer's
    school ceiling is applied server-side into the job, so the export can
    never contain more than the page shows."""
    http_method_names = ['post']
    required_permission = 'audit_log.read'

    def post(self, request):
        filters, _invalid = read_audit_filters(request.GET)
        job_filters = {key: value for key, value in {
            'module': filters['module'], 'action': filters['action'],
            'school': int(filters['school']) if filters['school'].isdigit() else None,
            'from': filters['from'], 'to': filters['to'], 'archived': filters['archived'],
        }.items() if value not in ('', None)}
        school_ids = accessible_school_ids(request.user, self.foundation_id, self.required_permission)
        if school_ids is not None:
            job_filters['school_ids'] = sorted(school_ids)
        create_export_job(
            report_key=REPORT_KEY_FOUNDATION_AUDIT,
            export_format=ExportJob.FORMAT_CSV,
            filters=job_filters,
            foundation_id=self.foundation_id,
            requested_by=str(request.user.id),
            requested_by_name=request.user.full_name or '',
        )
        messages.success(request, _("Ekspor diminta. Muat ulang halaman ini dalam beberapa menit untuk mengunduh berkasnya."))
        query = request.GET.urlencode()
        return redirect(f"{reverse('admin-audit')}{'?' + query if query else ''}")


class _SettingsAccessMixin(ConsolePermissionMixin):
    required_permission = 'school_config.write'

    def _audit_update(self, action, entity_type, entity_id, form, school_id=None):
        audit(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            actor_id=str(self.request.user.id),
            foundation_id=self.foundation_id,
            school_id=school_id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff=form.changed_diff(),
        )


class SettingsView(_SettingsAccessMixin, TemplateView):
    """Pengaturan sekolah: the foundation profile (editable by foundation
    admins only, matching the approval-threshold's governance weight) and the
    schools the viewer may configure, each linking to its own edit page."""
    template_name = 'pages/admin_settings.html'

    def get_context_data(self, form=None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        is_admin = is_foundation_admin(self.request.user, self.foundation_id)
        school_ids = accessible_school_ids(self.request.user, self.foundation_id, self.required_permission)
        schools = School.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True)
        if school_ids is not None:
            schools = schools.filter(id__in=school_ids)
        ctx.update({
            'is_foundation_admin': is_admin,
            'foundation_form': form or (
                FoundationProfileForm(instance=Foundation.objects.get(id=self.foundation_id)) if is_admin else None
            ),
            'schools': schools.order_by('name'),
        })
        return ctx


class FoundationProfileUpdateView(SettingsView):
    """POST /web/admin/settings/foundation/ — foundation admins only; an
    invalid submission re-renders the settings page with the form's errors."""
    http_method_names = ['post']

    def post(self, request):
        if not is_foundation_admin(request.user, self.foundation_id):
            return redirect('admin-settings')
        foundation = Foundation.objects.get(id=self.foundation_id)
        form = FoundationProfileForm(request.POST, instance=foundation)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        if form.has_changed():
            form.save()
            self._audit_update('foundation.settings.updated', 'Foundation', foundation.id, form)
        messages.success(request, _("Pengaturan yayasan disimpan."))
        return redirect('admin-settings')


class SchoolSettingsUpdateView(_SettingsAccessMixin, TemplateView):
    """GET/POST /web/admin/settings/schools/<id>/ — edit one school's profile."""
    template_name = 'pages/admin_school_settings.html'

    def _get_school(self, school_id):
        school_ids = accessible_school_ids(self.request.user, self.foundation_id, self.required_permission)
        if school_ids is not None and school_id not in school_ids:
            raise Http404
        return get_object_or_404(
            School.all_tenants, id=school_id, foundation_id=self.foundation_id, deleted_at__isnull=True,
        )

    def get_context_data(self, form=None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school = self._get_school(kwargs['school_id'])
        ctx.update({'school': school, 'form': form or SchoolSettingsForm(instance=school)})
        return ctx

    def post(self, request, school_id):
        school = self._get_school(school_id)
        form = SchoolSettingsForm(request.POST, instance=school)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, school_id=school_id), status=400)
        if form.has_changed():
            school.updated_by = str(request.user.id)
            form.save()
            self._audit_update('identity.school.updated', 'School', school.id, form, school_id=school.id)
        messages.success(request, _("Pengaturan sekolah disimpan."))
        return redirect('admin-settings')


class _FoundationAdminMixin(ConsolePermissionMixin):
    """School creation and (de)activation change the foundation's shape, not a
    school's profile, so they are foundation-admin-only even though the JSON
    SchoolViewSet gates them on school_config.write."""
    foundation_admin_only = True


class SchoolCreateView(_FoundationAdminMixin, TemplateView):
    """GET/POST /web/admin/settings/schools/new/ — create a school."""
    template_name = 'pages/admin_school_settings.html'

    def get_context_data(self, form=None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({'school': None, 'form': form or SchoolCreateForm()})
        return ctx

    def post(self, request):
        form = SchoolCreateForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        school = form.save(commit=False)
        school.foundation_id = self.foundation_id
        school.created_by = str(request.user.id)
        school.save()
        audit(
            action='identity.school.created',
            entity_type='School',
            entity_id=str(school.id),
            actor_id=str(request.user.id),
            foundation_id=self.foundation_id,
            school_id=school.id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={'name': {'after': school.name}, 'npsn': {'after': school.npsn}},
        )
        messages.success(request, _("Sekolah ditambahkan."))
        return redirect('admin-settings')


class SchoolActiveToggleView(_FoundationAdminMixin, View):
    """POST /web/admin/settings/schools/<id>/active/ with active=0|1.

    Deactivation flips is_active, not deleted_at: the cron jobs and
    portals that fan out over schools (absence marking, KPIs, operational
    consoles) skip inactive schools, while every academic and financial
    record stays intact and the school can be reactivated."""
    http_method_names = ['post']

    def post(self, request, school_id):
        school = get_object_or_404(
            School.all_tenants, id=school_id, foundation_id=self.foundation_id, deleted_at__isnull=True,
        )
        active = request.POST.get('active') == '1'
        if school.is_active != active:
            school.is_active = active
            school.updated_by = str(request.user.id)
            school.save(update_fields=['is_active', 'updated_at', 'updated_by'])
            audit(
                action='identity.school.activated' if active else 'identity.school.deactivated',
                entity_type='School',
                entity_id=str(school.id),
                actor_id=str(request.user.id),
                foundation_id=self.foundation_id,
                school_id=school.id,
                ip_address=request.META.get('REMOTE_ADDR'),
                diff={'is_active': {'before': not active, 'after': active}},
            )
        messages.success(request, _("Sekolah diaktifkan.") if active else _("Sekolah dinonaktifkan."))
        return redirect('admin-settings')

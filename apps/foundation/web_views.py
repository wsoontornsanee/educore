"""Administrasi console pages owned by apps.foundation, mounted under /web/admin/.

Session-auth HTML surfaces over the same services the JSON views use, so the
console and the API can never disagree about what a filter or a write means.
"""
import json

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from apps.core.services import audit
from apps.foundation.forms import FoundationProfileForm, SchoolSettingsForm
from apps.foundation.services import filter_foundation_audit_events
from apps.identity.console_access import ConsolePermissionMixin, accessible_school_ids, paginate_queryset
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import is_foundation_admin


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

        params = self.request.GET
        filters = {key: params.get(key, '').strip() for key in ('module', 'action', 'school', 'from', 'to')}
        invalid_dates = [key for key in ('from', 'to') if filters[key] and not parse_date(filters[key])]
        for key in invalid_dates:
            filters[key] = ''

        events = filter_foundation_audit_events(
            foundation_id,
            school_id=int(filters['school']) if filters['school'].isdigit() else None,
            module=filters['module'] or None,
            action=filters['action'] or None,
            from_date=filters['from'] or None,
            to_date=filters['to'] or None,
        )
        if school_ids is not None:
            events = events.filter(school_id__in=school_ids)

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
        })
        return ctx


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

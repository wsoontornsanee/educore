"""Session-auth internal management pages for apps.status — mirrors
apps.academic.views' PermissionSlipWebAccessMixin (APIView + render()) pattern.
Gated by the platform-wide 'status.write' permission (Task 2), not the
tenant-scoped RoleAssignment table.
"""
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from rest_framework.views import APIView

from apps.core.job_health import evaluate_job_health
from apps.identity.permissions import HasRequiredPermission
from .forms import IncidentCreateForm
from .models import ServiceComponent, StatusIncident
from .services import classify_platform_operators, create_incident, update_incident


class StatusManageAccessMixin:
    """Shared gating: session-authenticated users holding the platform status.write permission."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'status.write'


class StatusManagePageView(StatusManageAccessMixin, APIView):
    """GET /web/status/manage/ — list components and incidents for editing."""

    def get(self, request):
        components = ServiceComponent.objects.all()
        incidents = StatusIncident.objects.all()
        reachable, unreachable = classify_platform_operators()
        return render(request, 'status/manage.html', {
            'components': components,
            'incidents': incidents,
            'jobs': evaluate_job_health(),
            'alert_recipient_count': len(reachable),
            'unreachable_operators': unreachable,
        })


class StatusComponentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/components/<id>/ — set or clear a component's manual_status override."""

    def post(self, request, component_id):
        component = get_object_or_404(ServiceComponent, id=component_id)
        manual_status = request.POST.get('manual_status', '').strip()
        if manual_status and manual_status not in dict(ServiceComponent.STATUS_CHOICES):
            return HttpResponseBadRequest('manual_status tidak valid')
        component.manual_status = manual_status or None
        component.save(update_fields=['manual_status'])
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentCreateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/create/ — create a new incident."""

    def post(self, request):
        form = IncidentCreateForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest('Data insiden tidak valid')
        cleaned = form.cleaned_data
        create_incident(
            severity=cleaned['severity'],
            title_id=cleaned['title_id'], title_en=cleaned['title_en'],
            body_id=cleaned['body_id'], body_en=cleaned['body_en'],
            occurred_at=timezone.now(),
            duration_minutes=cleaned['duration_minutes'],
            affected_component_ids=[c.id for c in cleaned['affected_components']],
            published=cleaned['published'],
            actor=request.user,
        )
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/<id>/ — toggle publish or edit an existing incident."""

    def post(self, request, incident_id):
        incident = get_object_or_404(StatusIncident, id=incident_id)
        update_incident(incident, actor=request.user, published=bool(request.POST.get('published')))
        return HttpResponseRedirect(reverse('status_manage:page'))

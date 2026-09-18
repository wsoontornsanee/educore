"""Session-auth internal management pages for apps.status — mirrors
apps.academic.views' PermissionSlipWebAccessMixin (APIView + render()) pattern.
Gated by the platform-wide 'status.write' permission (Task 2), not the
tenant-scoped RoleAssignment table.
"""
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from rest_framework.views import APIView

from apps.identity.permissions import HasRequiredPermission
from .models import ServiceComponent, StatusIncident
from .services import create_incident, update_incident


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
        return render(request, 'status/manage.html', {
            'components': components,
            'incidents': incidents,
        })


class StatusComponentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/components/<id>/ — set or clear a component's manual_status override."""

    def post(self, request, component_id):
        component = get_object_or_404(ServiceComponent, id=component_id)
        manual_status = request.POST.get('manual_status', '').strip()
        component.manual_status = manual_status or None
        component.save(update_fields=['manual_status'])
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentCreateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/create/ — create a new incident."""

    def post(self, request):
        affected_ids = request.POST.getlist('affected_components')
        create_incident(
            severity=request.POST['severity'],
            title_id=request.POST['title_id'], title_en=request.POST['title_en'],
            body_id=request.POST['body_id'], body_en=request.POST['body_en'],
            occurred_at=timezone.now(),
            duration_minutes=int(request.POST['duration_minutes']),
            affected_component_ids=[int(cid) for cid in affected_ids],
            published=bool(request.POST.get('published')),
            actor=request.user,
        )
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/<id>/ — toggle publish or edit an existing incident."""

    def post(self, request, incident_id):
        incident = get_object_or_404(StatusIncident, id=incident_id)
        update_incident(incident, actor=request.user, published=bool(request.POST.get('published')))
        return HttpResponseRedirect(reverse('status_manage:page'))

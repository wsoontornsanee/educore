"""Public (no-auth, no-tenant) service status page — mirrors apps.marketing's TemplateView pattern."""
from django.views.generic import TemplateView

from .models import ServiceComponent
from .services import (
    get_average_latency_ms, get_component_bars, get_component_status,
    get_open_component_count, get_uptime_percentage, list_published_incidents,
)
from .strings import get_status_strings


class StatusPageView(TemplateView):
    template_name = 'status/status.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lang_code = self.request.GET.get('lang', 'ID')
        t = get_status_strings(lang_code)
        ctx['t'] = t
        ctx['lang_code'] = 'EN' if lang_code.upper() == 'EN' else 'ID'

        components = []
        any_degraded = False
        any_down = False
        for component in ServiceComponent.objects.all():
            status = get_component_status(component)
            if status == ServiceComponent.STATUS_DOWN:
                any_down = True
            elif status == ServiceComponent.STATUS_DEGRADED:
                any_degraded = True
            status_label = {
                ServiceComponent.STATUS_OPERATIONAL: t['st_ok'],
                ServiceComponent.STATUS_DEGRADED: t['st_degraded'],
                ServiceComponent.STATUS_DOWN: t['st_down'],
            }[status]
            status_dot = {
                ServiceComponent.STATUS_OPERATIONAL: '#0E7A4F',
                ServiceComponent.STATUS_DEGRADED: '#B56A00',
                ServiceComponent.STATUS_DOWN: '#B3261E',
            }[status]
            components.append({
                'name': component.name_en if ctx['lang_code'] == 'EN' else component.name_id,
                'note': component.note_en if ctx['lang_code'] == 'EN' else component.note_id,
                'status': status_label,
                'dot': status_dot,
                'bars': get_component_bars(component),
            })
        ctx['components'] = components
        ctx['banner_ok'] = not (any_degraded or any_down)
        ctx['banner_text'] = t['st_banner'] if ctx['banner_ok'] else t['st_banner_degraded']

        ctx['uptime_pct'] = get_uptime_percentage()
        ctx['avg_latency_ms'] = get_average_latency_ms()
        ctx['open_component_count'] = get_open_component_count()

        incidents = []
        for incident in list_published_incidents():
            incidents.append({
                'severity': t['sev_minor'] if incident.severity == 'MINOR' else t['sev_major'] if incident.severity == 'MAJOR' else t['sev_maint'],
                'title': incident.title_en if ctx['lang_code'] == 'EN' else incident.title_id,
                'body': incident.body_en if ctx['lang_code'] == 'EN' else incident.body_id,
                'occurred_at': incident.occurred_at,
                'duration_minutes': incident.duration_minutes,
                'affected': [
                    c.name_en if ctx['lang_code'] == 'EN' else c.name_id
                    for c in incident.affected_components.all()
                ],
            })
        ctx['incidents'] = incidents
        return ctx

"""Public (no-auth, no-tenant) service status page — mirrors apps.marketing's TemplateView pattern."""
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import get_language
from django.views.generic import TemplateView
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.throttling import StatusSubscribeThrottle

from .models import ServiceComponent, StatusIncident
from .services import (
    BAR_COLORS, get_average_latency_ms, get_component_bars, get_component_status,
    get_open_component_count, get_uptime_percentage, list_published_incidents,
    subscribe_email,
)
from .strings import get_status_strings

STATUS_LABEL_KEYS = {
    ServiceComponent.STATUS_OPERATIONAL: 'st_ok',
    ServiceComponent.STATUS_DEGRADED: 'st_degraded',
    ServiceComponent.STATUS_DOWN: 'st_down',
}

# Reuse services.BAR_COLORS (the single source of truth for the 3 status
# hex colors) instead of a second, independently-maintained dict.
STATUS_DOTS = BAR_COLORS

SEVERITY_LABEL_KEYS = {
    StatusIncident.SEVERITY_MINOR: 'sev_minor',
    StatusIncident.SEVERITY_MAJOR: 'sev_major',
    StatusIncident.SEVERITY_MAINTENANCE: 'sev_maint',
}


class StatusPageView(TemplateView):
    template_name = 'status/status.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Follow the site's real active language (set by LocaleMiddleware from
        # the site-wide language-switcher cookie, same mechanism
        # marketing/base.html's nav/footer chrome uses) by default; an
        # explicit ?lang= query param — e.g. a future status-page-specific
        # "view in English" link — overrides it.
        override = self.request.GET.get('lang')
        lang_code = override if override else get_language()
        t = get_status_strings(lang_code)
        ctx['t'] = t
        ctx['lang_code'] = 'EN' if (lang_code or '').upper() == 'EN' else 'ID'

        components = []
        any_degraded = False
        any_down = False
        for component in ServiceComponent.objects.all():
            status = get_component_status(component)
            if status == ServiceComponent.STATUS_DOWN:
                any_down = True
            elif status == ServiceComponent.STATUS_DEGRADED:
                any_degraded = True
            # .get(..., fallback to OPERATIONAL's entry) rather than a bare
            # [status] lookup: a stray/invalid status value already sitting
            # in the DB (manual_status or a DailyComponentStatus row) should
            # degrade gracefully instead of 500ing this public page.
            status_label = t[STATUS_LABEL_KEYS.get(status, STATUS_LABEL_KEYS[ServiceComponent.STATUS_OPERATIONAL])]
            status_dot = STATUS_DOTS.get(status, STATUS_DOTS[ServiceComponent.STATUS_OPERATIONAL])
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
                # .get(..., fallback to MAINTENANCE's key) against the
                # SEVERITY_* model constants, not magic strings — an
                # unexpected severity value falls back to a labeled,
                # explicit choice rather than silently rendering as
                # "Maintenance" by default.
                'severity': t[SEVERITY_LABEL_KEYS.get(incident.severity, SEVERITY_LABEL_KEYS[StatusIncident.SEVERITY_MAINTENANCE])],
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


class StatusSubscribeView(APIView):
    """Public, unauthenticated write endpoint — validated and rate-limited
    like this repo's other public write surfaces (see
    apps.core.throttling's OtpRequestIPThrottle for the pattern this
    follows: a conservative per-IP ScopedRateThrottle)."""
    http_method_names = ['post']
    permission_classes = [AllowAny]
    throttle_classes = [StatusSubscribeThrottle]
    throttle_scope = 'status_subscribe'

    def post(self, request, *args, **kwargs):
        email = request.POST.get('email', '').strip()
        if not email:
            return HttpResponseBadRequest('email is required')
        try:
            validate_email(email)
        except ValidationError:
            return HttpResponseBadRequest('email tidak valid')
        subscribe_email(email)
        return HttpResponseRedirect(reverse('status:page'))

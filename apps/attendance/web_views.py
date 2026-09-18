"""Web (session-auth HTMX) views for the Kehadiran & gerbang console.

A browser-side wall monitor over the same data the gate JSON API serves:
`get_live_gate_feed` (spec/05 §4 ATT-013, spec/17 §7.2) for the scan feed and
device health, `get_today_attendance_counts` for the daily-status strip.
Read-only: manual check-in and attendance overrides stay on the JSON API.
"""
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin

from .services import get_live_gate_feed, get_today_attendance_counts

# Newest-first rows shown in the feed; the service already caps at its own limit.
FEED_ROW_LIMIT = 50


class GateConsoleAccessMixin(StaffConsoleMixin):
    def get_required_permission(self):
        return 'attendance.read'


class GateConsolePageView(GateConsoleAccessMixin, APIView):
    """GET /web/attendance/gate/ — full page; the live feed is an HTMX fragment."""

    def get(self, request):
        _foundation_id, schools, school = self.console_context(request)
        return render(request, 'pages/gate_console_page.html', {
            'schools': schools,
            'school': school,
        })


class GateConsoleFeedView(GateConsoleAccessMixin, APIView):
    """GET /web/attendance/gate/feed/?school_id= — polled fragment (ARC-015: 3s cursor-less refresh)."""

    def get(self, request):
        foundation_id, _schools, school = self.console_context(request)
        if school is None:
            return render(request, 'components/_gate_live_feed.html', {'school': None})

        feed = get_live_gate_feed(foundation_id=foundation_id, school_id=school.id, limit=FEED_ROW_LIMIT)
        events = list(reversed(feed['events']))  # service returns chronological; feed reads newest-first
        for event in events:
            event['occurred_at_dt'] = parse_datetime(event['occurred_at'])
        for device in feed['devices_summary']:
            device['is_online'] = device['status'] == 'ONLINE'
        return render(request, 'components/_gate_live_feed.html', {
            'school': school,
            'events': events,
            'rejected_count': len(feed['alerts']),
            'devices': feed['devices_summary'],
            'attendance_counts': get_today_attendance_counts(foundation_id, school),
        })

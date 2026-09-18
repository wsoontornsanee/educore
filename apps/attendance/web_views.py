"""Web (session-auth HTMX) views for the Kehadiran & gerbang console.

A browser-side wall monitor over the same data the gate JSON API serves:
`get_live_gate_feed` (spec/05 §4 ATT-013, spec/17 §7.2) for the scan feed and
device health, `get_today_attendance_counts` for the daily-status strip.
The live feed is read-only; the two "piket" actions below it (manual
check-in for a forgotten card, and a day-status override) call the same
services as the JSON API — `manual_gate_checkin` / `override_attendance_day`,
which already enforce the mandatory reason note and write the audit event —
behind `attendance.write`.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin, permitted_schools
from apps.identity.models import Student

from .models import AttendanceDay, AttendanceStatus, GateDirection
from .services import (
    get_live_gate_feed, get_today_attendance_counts, manual_gate_checkin, override_attendance_day,
)

# Newest-first rows shown in the feed; the service already caps at its own limit.
FEED_ROW_LIMIT = 50


class GateConsoleAccessMixin(StaffConsoleMixin):
    def get_required_permission(self):
        return 'attendance.read'


class GateConsolePageView(GateConsoleAccessMixin, APIView):
    """GET /web/attendance/gate/ — full page; the live feed is an HTMX fragment."""

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        can_write = school is not None and any(
            s.id == school.id for s in permitted_schools(request.user, foundation_id, 'attendance.write')
        )
        return render(request, 'pages/gate_console_page.html', {
            'schools': schools,
            'school': school,
            'can_write': can_write,
            'directions': GateDirection.choices,
            'statuses': AttendanceStatus.choices,
            'today': timezone.localdate(),
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


class _GateActionView(StaffConsoleMixin, APIView):
    """POST base for the piket actions: `attendance.write`, school taken from
    ?school_id= and validated against the schools this user may write for
    (console_context 404s any other school). Every outcome — success or a
    service ValidationError — is flashed and the browser is sent back to the
    gate page for the same school."""

    def get_required_permission(self):
        return 'attendance.write'

    def _find_student(self, foundation_id, school, nis):
        nis = (nis or '').strip()
        if not nis:
            return None
        return Student.objects.filter(
            foundation_id=foundation_id, school_id=school.id, nis=nis, deleted_at__isnull=True,
        ).first()

    def _done(self, request, school):
        return redirect(f"{reverse('attendance-gate-console-page')}?school_id={school.id}")

    def _fail(self, request, school, exc_or_msg):
        text = '; '.join(exc_or_msg.messages) if isinstance(exc_or_msg, ValidationError) else str(exc_or_msg)
        messages.error(request, text)
        return self._done(request, school)


class GateManualCheckinView(_GateActionView):
    """POST /web/attendance/gate/manual/?school_id= — check a student in/out by NIS."""

    def post(self, request):
        foundation_id, _schools, school = self.console_context(request)
        student = self._find_student(foundation_id, school, request.POST.get('nis'))
        if student is None:
            return self._fail(request, school, _("Siswa dengan NIS tersebut tidak ditemukan di sekolah ini."))
        direction = request.POST.get('direction', GateDirection.IN)
        if direction not in GateDirection.values:
            return self._fail(request, school, _("Arah tidak valid."))
        try:
            manual_gate_checkin(
                foundation_id=foundation_id, school_id=school.id, student_id=student.id,
                direction=direction, reason=request.POST.get('reason', ''), user=request.user,
            )
        except ValidationError as exc:
            return self._fail(request, school, exc)
        messages.success(request, _("Check-in manual dicatat."))
        return self._done(request, school)


class GateDayOverrideView(_GateActionView):
    """POST /web/attendance/gate/override/?school_id= — override a student's
    derived day status. The day must already exist (created by a scan, the
    cutoff job or a manual check-in); a mandatory note is enforced by the
    service."""

    def post(self, request):
        foundation_id, _schools, school = self.console_context(request)
        student = self._find_student(foundation_id, school, request.POST.get('nis'))
        if student is None:
            return self._fail(request, school, _("Siswa dengan NIS tersebut tidak ditemukan di sekolah ini."))
        new_status = request.POST.get('status', '')
        if new_status not in AttendanceStatus.values:
            return self._fail(request, school, _("Status kehadiran tidak valid."))
        date = parse_date(request.POST.get('date') or '') or timezone.localdate()
        if date > timezone.localdate():
            return self._fail(request, school, _("Tanggal tidak boleh di masa depan."))
        day = AttendanceDay.objects.filter(
            foundation_id=foundation_id, school_id=school.id, student_id=student.id,
            date=date, deleted_at__isnull=True,
        ).first()
        if day is None:
            return self._fail(request, school, _("Belum ada catatan kehadiran siswa ini pada tanggal tersebut."))
        try:
            override_attendance_day(
                foundation_id=foundation_id, attendance_day=day, new_status=new_status,
                note=request.POST.get('note', ''), user=request.user,
            )
        except ValidationError as exc:
            return self._fail(request, school, exc)
        messages.success(request, _("Status kehadiran diperbarui."))
        return self._done(request, school)

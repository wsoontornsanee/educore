"""Kehadiran & gerbang web console (GET /web/attendance/gate/ + HTMX feed fragment)."""
import uuid

from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import (
    AttendanceDay, AttendanceStatus, GateDirection, GateEvent, GateEventStatus, GateMethod,
)
from apps.attendance.services import get_today_attendance_counts
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import set_current_foundation_id

FEED_URL = '/web/attendance/gate/feed/'


class GateConsoleWebTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Gerbang Web")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.device = Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, name="Gerbang Utama", device_code="GT-01",
            device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN, status=DeviceStatus.ONLINE,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def _scan(self, status=GateEventStatus.ACCEPTED, reject_reason=''):
        return GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, method=GateMethod.RFID,
            occurred_at=timezone.now(), status=status, reject_reason=reject_reason,
        )

    def test_page_shell_polls_the_feed_for_the_selected_school(self):
        res = self.client.get('/web/attendance/gate/')
        self.assertContains(res, 'Kehadiran &amp; gerbang')
        self.assertContains(res, f'hx-get="{FEED_URL}?school_id={self.school.id}"')
        self.assertContains(res, 'every 3s')

    def test_feed_lists_todays_scan_and_device(self):
        self._scan()
        res = self.client.get(FEED_URL)
        self.assertContains(res, 'Andi Wijaya')
        self.assertContains(res, 'Diterima')
        self.assertContains(res, 'Gerbang Utama')
        self.assertContains(res, 'GT-01')

    def test_feed_flags_rejected_scan_and_counts_it(self):
        self._scan(status=GateEventStatus.REJECTED, reject_reason='CARD_REVOKED')
        res = self.client.get(FEED_URL)
        self.assertContains(res, 'Ditolak')
        self.assertContains(res, 'CARD_REVOKED')
        self.assertEqual(res.context['rejected_count'], 1)

    def test_feed_empty_states(self):
        Device.objects.filter(pk=self.device.pk).update(deleted_at=timezone.now())
        res = self.client.get(FEED_URL)
        self.assertContains(res, 'Belum ada pindai gerbang hari ini.')
        self.assertContains(res, 'Belum ada perangkat gerbang terdaftar')

    def test_feed_shows_daily_attendance_counts(self):
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=timezone.localdate(), status=AttendanceStatus.TERLAMBAT,
        )
        res = self.client.get(FEED_URL)
        self.assertEqual(res.context['attendance_counts']['TERLAMBAT'], 1)
        self.assertEqual(res.context['attendance_counts']['HADIR'], 0)

    def test_feed_does_not_show_other_school_scans(self):
        from apps.identity.models import School
        other = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Lain", npsn="70000001", level=School.LEVEL_SMP,
        )
        other_device = Device.objects.create(
            foundation_id=self.foundation.id, school=other, name="Gerbang Lain", device_code="GT-99",
            device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN, status=DeviceStatus.ONLINE,
        )
        GateEvent.objects.create(
            foundation_id=self.foundation.id, school=other, device=other_device, student=None, event_uuid=uuid.uuid4(),
            raw_uid='UID-LAIN', direction=GateDirection.IN, method=GateMethod.RFID,
            occurred_at=timezone.now(), status=GateEventStatus.REJECTED, reject_reason='UNKNOWN_CARD',
        )
        res = self.client.get(FEED_URL)
        self.assertNotContains(res, 'UNKNOWN_CARD')


class TodayAttendanceCountsTests(TestCase):
    def test_every_status_key_present_and_zero_by_default(self):
        fx = build_academic_fixture("Yayasan Hitung Hadir")
        counts = get_today_attendance_counts(fx['foundation'].id, fx['school'])
        self.assertEqual(set(counts), set(AttendanceStatus.values))
        self.assertEqual(sum(counts.values()), 0)


class GateConsoleWriteActionTests(TestCase):
    """Piket actions: manual check-in and day override, gated by attendance.write."""
    MANUAL = '/web/attendance/gate/manual/'
    OVERRIDE = '/web/attendance/gate/override/'

    def setUp(self):
        from apps.core.models import AuditEvent
        from apps.identity.models import Person, Staff, User
        self.AuditEvent = AuditEvent
        self.fx = build_academic_fixture("Yayasan Piket")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        # counsellor: attendance.read only, plus a Staff profile so the console mixin lets them in
        reader = User.objects.create(foundation_id=self.foundation.id, phone_e164='+6281200099001', full_name='Konselor')
        Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Konselor'),
            user=reader, school=self.school, join_date=timezone.localdate(),
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=reader, role='counsellor',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.reader = reader
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])
        self.url = lambda base: f'{base}?school_id={self.school.id}'

    def _flash(self, response):
        return [str(m) for m in response.wsgi_request._messages] if hasattr(response, 'wsgi_request') else []

    def test_page_shows_piket_forms_only_with_write_permission(self):
        res = self.client.get('/web/attendance/gate/')
        self.assertContains(res, 'Tindakan piket')
        self.client.force_authenticate(user=self.reader)
        self.assertNotContains(self.client.get('/web/attendance/gate/'), 'Tindakan piket')

    def test_manual_checkin_creates_event_day_and_flashes(self):
        res = self.client.post(self.url(self.MANUAL), {'nis': self.student.nis, 'direction': 'IN', 'reason': 'Kartu tertinggal'})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(GateEvent.all_tenants.filter(student=self.student, method=GateMethod.MANUAL).count(), 1)
        self.assertTrue(AttendanceDay.all_tenants.filter(student=self.student, date=timezone.localdate()).exists())
        self.assertIn('Check-in manual dicatat.', [str(m) for m in res.wsgi_request._messages])

    def test_manual_checkin_requires_reason_and_known_nis(self):
        res = self.client.post(self.url(self.MANUAL), {'nis': self.student.nis, 'direction': 'IN', 'reason': '  '})
        self.assertEqual(GateEvent.all_tenants.filter(method=GateMethod.MANUAL).count(), 0)
        self.assertTrue(any('Alasan check-in manual wajib' in m for m in [str(x) for x in res.wsgi_request._messages]))
        res = self.client.post(self.url(self.MANUAL), {'nis': 'NOPE', 'direction': 'IN', 'reason': 'x'})
        self.assertTrue(any('tidak ditemukan' in str(m) for m in res.wsgi_request._messages))
        res = self.client.post(self.url(self.MANUAL), {'nis': self.student.nis, 'direction': 'SIDEWAYS', 'reason': 'x'})
        self.assertEqual(GateEvent.all_tenants.filter(method=GateMethod.MANUAL).count(), 0)

    def test_read_only_user_cannot_write(self):
        self.client.force_authenticate(user=self.reader)
        res = self.client.post(self.url(self.MANUAL), {'nis': self.student.nis, 'direction': 'IN', 'reason': 'x'})
        self.assertRedirects(res, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertEqual(GateEvent.all_tenants.filter(method=GateMethod.MANUAL).count(), 0)
        res = self.client.post(self.url(self.OVERRIDE), {'nis': self.student.nis, 'status': 'SAKIT', 'note': 'x'})
        self.assertRedirects(res, reverse('web-console-home'), fetch_redirect_response=False)

    def test_student_of_another_school_or_foundation_is_not_found(self):
        other = build_academic_fixture("Yayasan Piket Lain")
        set_current_foundation_id(self.foundation.id)
        res = self.client.post(self.url(self.MANUAL), {'nis': other['student'].nis, 'direction': 'IN', 'reason': 'x'})
        if other['student'].nis != self.student.nis:
            self.assertEqual(GateEvent.all_tenants.filter(method=GateMethod.MANUAL).count(), 0)
        # a school the user cannot write for is refused outright
        res = self.client.post(f"{self.MANUAL}?school_id={other['school'].id}", {'nis': 'x', 'reason': 'x'})
        self.assertIn(res.status_code, (302, 404))

    def test_override_changes_status_with_note_and_audits(self):
        day = AttendanceDay.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=timezone.localdate(), status=AttendanceStatus.ALPA,
        )
        res = self.client.post(self.url(self.OVERRIDE), {'nis': self.student.nis, 'status': 'SAKIT', 'note': 'Surat dokter'})
        self.assertEqual(res.status_code, 302)
        day.refresh_from_db()
        self.assertEqual((day.status, day.original_status, day.is_override, day.note), ('SAKIT', 'ALPA', True, 'Surat dokter'))
        self.assertTrue(self.AuditEvent.objects.filter(action='attendance.day.overridden', entity_id=str(day.id)).exists())

    def test_override_validation_failures_change_nothing(self):
        day = AttendanceDay.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=timezone.localdate(), status=AttendanceStatus.ALPA,
        )
        cases = [
            {'nis': self.student.nis, 'status': 'SAKIT', 'note': ''},                       # note required
            {'nis': self.student.nis, 'status': 'BOGUS', 'note': 'x'},                      # bad status
            {'nis': self.student.nis, 'status': 'SAKIT', 'note': 'x', 'date': '2999-01-01'},  # future
            {'nis': self.student.nis, 'status': 'SAKIT', 'note': 'x', 'date': '2020-01-01'},  # no day row
        ]
        for payload in cases:
            self.client.post(self.url(self.OVERRIDE), payload)
        day.refresh_from_db()
        self.assertEqual((day.status, day.is_override), ('ALPA', False))
        self.assertFalse(self.AuditEvent.objects.filter(action='attendance.day.overridden').exists())


class AttendanceDayUsesSchoolTimezoneTests(TestCase):
    """update_daily_attendance_from_gate must derive the day and the
    HADIR/TERLAMBAT cutoff from the school's local wall clock, not from the
    UTC representation of the scan instant."""

    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Zona Waktu")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)

    def _checkin(self, iso):
        from apps.attendance.services import manual_gate_checkin
        import dateutil.parser
        return manual_gate_checkin(
            foundation_id=self.foundation.id, school_id=self.school.id, student_id=self.student.id,
            direction='IN', occurred_at=dateutil.parser.isoparse(iso), reason='uji',
        )['attendance_day']

    def test_early_wib_scan_lands_on_the_local_date_and_is_on_time(self):
        # 06:30 WIB on 2026-09-19 == 23:30 UTC on 2026-09-18
        day = self._checkin('2026-09-18T23:30:00+00:00')
        self.assertEqual(day.date.isoformat(), '2026-09-19')
        self.assertEqual(day.status, AttendanceStatus.HADIR)

    def test_wib_scan_after_cutoff_is_late_even_though_utc_time_is_early(self):
        # 07:30 WIB == 00:30 UTC: bare UTC .time() would read 00:30 <= 07:15 => HADIR
        day = self._checkin('2026-09-19T00:30:00+00:00')
        self.assertEqual(day.date.isoformat(), '2026-09-19')
        self.assertEqual(day.status, AttendanceStatus.TERLAMBAT)

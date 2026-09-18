"""Kehadiran & gerbang web console (GET /web/attendance/gate/ + HTMX feed fragment)."""
import uuid

from django.test import TestCase
from django.utils import timezone
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

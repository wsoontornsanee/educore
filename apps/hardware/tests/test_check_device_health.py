import datetime
import zoneinfo
from unittest.mock import patch

from django.test import TestCase

from apps.core.models import AuditEvent
from apps.hardware.models import Device, DeviceClass, DeviceStatus, DeviceUptimeDay
from apps.hardware.services import check_device_health
from apps.identity.models import Foundation, Person, RoleAssignment, School, User
from apps.notifications.models import NotificationCategory, NotificationIntent
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

# A fixed Wednesday noon WIB, safely inside the default 06:00-18:00
# operational window - `timezone.now()` is patched to this everywhere below
# so heartbeat timestamps (also derived from it) stay stable regardless of
# the real wall-clock date the suite happens to run on.
FIXED_NOON_WIB = datetime.datetime(2026, 9, 16, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo('Asia/Jakarta'))


class CheckDeviceHealthTests(TestCase):
    """spec/12 §3 HW-007/HW-013: stale-heartbeat detection and admin alerting."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Nusantara", brand_name="Nusantara",
            npwp="01.333.444.5-666.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Nusantara", npsn="30500001",
            level=School.LEVEL_SMP, timezone='Asia/Jakarta',
        )
        self.admin_person = Person.objects.create(foundation_id=self.foundation.id, full_name="Admin Sekolah")
        self.admin_user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164="+6281200000001",
            email="admin@nusantara.sch.id", full_name="Admin Sekolah",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.admin_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN, scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _make_device(self, device_class=DeviceClass.GATE_READER, status=DeviceStatus.ONLINE,
                      minutes_ago=30, now=FIXED_NOON_WIB):
        import uuid
        return Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code=f"DEV-{uuid.uuid4().hex[:8]}",
            name="Gerbang Utama", device_class=device_class, status=status,
            last_heartbeat_at=now - datetime.timedelta(minutes=minutes_ago),
        )

    def test_marks_stale_device_offline(self):
        device = self._make_device(minutes_ago=30)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            result = check_device_health(timeout_minutes=15)

        self.assertEqual(result, {'checked': 1, 'marked_offline': 1, 'alerts_dispatched': 1})
        device.refresh_from_db()
        self.assertEqual(device.status, DeviceStatus.OFFLINE)
        self.assertTrue(AuditEvent.objects.filter(entity_type='Device', entity_id=device.id).exists())

    def test_does_not_flag_device_within_timeout(self):
        self._make_device(minutes_ago=5)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            result = check_device_health(timeout_minutes=15)

        self.assertEqual(result, {'checked': 0, 'marked_offline': 0, 'alerts_dispatched': 0})

    def test_alerts_school_admin_for_critical_gate(self):
        self._make_device(device_class=DeviceClass.GATE_READER, minutes_ago=30)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            check_device_health(timeout_minutes=15)

        intent = NotificationIntent.objects.get(category=NotificationCategory.DEVICE_OFFLINE)
        self.assertEqual(intent.recipient_user_id, self.admin_user.id)

    def test_every_school_admin_is_alerted_not_just_the_first(self):
        second = User.objects.create(
            foundation_id=self.foundation.id, phone_e164="+6281200000002",
            email="admin2@nusantara.sch.id", full_name="Admin Dua",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=second, role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self._make_device(minutes_ago=30)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            check_device_health(timeout_minutes=15)
        intents = NotificationIntent.all_tenants.filter(category=NotificationCategory.DEVICE_OFFLINE)
        self.assertEqual({i.recipient_user_id for i in intents}, {self.admin_user.id, second.id})
        self.assertEqual(intents.count(), 2)

    def test_does_not_alert_for_non_critical_device_class(self):
        self._make_device(device_class=DeviceClass.KIOSK, minutes_ago=30)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            result = check_device_health(timeout_minutes=15)

        self.assertEqual(result['marked_offline'], 1)
        self.assertEqual(result['alerts_dispatched'], 0)
        self.assertFalse(NotificationIntent.objects.filter(category=NotificationCategory.DEVICE_OFFLINE).exists())

    def test_does_not_alert_outside_operational_hours(self):
        late_night = FIXED_NOON_WIB.replace(hour=2)
        self._make_device(device_class=DeviceClass.GATE_READER, minutes_ago=30, now=late_night)
        with patch('apps.hardware.services.timezone.now', return_value=late_night):
            result = check_device_health(timeout_minutes=15)

        self.assertEqual(result['marked_offline'], 1)
        self.assertEqual(result['alerts_dispatched'], 0)

    def test_ignores_retired_and_already_offline_devices(self):
        self._make_device(status=DeviceStatus.RETIRED, minutes_ago=999)
        self._make_device(status=DeviceStatus.OFFLINE, minutes_ago=999)
        with patch('apps.hardware.services.timezone.now', return_value=FIXED_NOON_WIB):
            result = check_device_health(timeout_minutes=15)

        self.assertEqual(result, {'checked': 0, 'marked_offline': 0, 'alerts_dispatched': 0})

    def test_heartbeat_restores_device_to_online(self):
        """Recovery is the existing DeviceViewSet.heartbeat behavior, not this command's - assert it still holds."""
        from rest_framework.test import APIClient
        device = self._make_device(status=DeviceStatus.OFFLINE, minutes_ago=999)
        client = APIClient()
        client.force_authenticate(user=self.admin_user)
        resp = client.post(f'/api/v1/devices/{device.id}/heartbeat/', data={'status': 'ONLINE'}, format='json')
        self.assertEqual(resp.status_code, 200)
        device.refresh_from_db()
        self.assertEqual(device.status, DeviceStatus.ONLINE)

    # RPT-013: each health run adds one availability sample per gate/face device in operational hours.

    def _day(self):
        return DeviceUptimeDay.all_tenants.get(school=self.school, date=datetime.date(2026, 9, 16))

    def _run(self, now=FIXED_NOON_WIB):
        with patch('apps.hardware.services.timezone.now', return_value=now):
            check_device_health(timeout_minutes=15)

    def test_counts_reachable_gates_after_the_stale_sweep(self):
        self._make_device(minutes_ago=5)                                     # up
        self._make_device(device_class=DeviceClass.FACE_TERMINAL, status=DeviceStatus.DEGRADED, minutes_ago=5)  # up
        self._make_device(minutes_ago=30)                                    # stale: swept to OFFLINE, down
        self._run()
        day = self._day()
        self.assertEqual((day.samples, day.up_samples), (3, 2))

    def test_runs_accumulate_on_the_same_day_row(self):
        self._make_device(minutes_ago=5)
        self._run()
        self._run(FIXED_NOON_WIB + datetime.timedelta(minutes=10))
        self.assertEqual(self._day().samples, 2)

    def test_other_device_classes_and_retired_gates_are_not_sampled(self):
        self._make_device(device_class=DeviceClass.KIOSK, minutes_ago=5)
        self._make_device(device_class=DeviceClass.POS_TERMINAL, minutes_ago=5)
        self._make_device(status=DeviceStatus.RETIRED, minutes_ago=5)
        self._run()
        self.assertFalse(DeviceUptimeDay.all_tenants.exists())

    def test_nothing_is_sampled_outside_operational_hours_or_on_sunday(self):
        self._make_device(minutes_ago=5)
        self._run(FIXED_NOON_WIB.replace(hour=22))
        self._run(FIXED_NOON_WIB + datetime.timedelta(days=4))   # Sunday 2026-09-20
        self.assertFalse(DeviceUptimeDay.all_tenants.exists())

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Foundation, Person, RoleAssignment, School, User
from educore.middleware.tenancy import set_current_foundation_id


class DeviceManagementTests(TestCase):
    """
    Test suite for hardware devices, tenancy isolation, and heartbeats (spec/12 §2, §4, §7).
    """

    def setUp(self):
        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Al-Hikmah",
            brand_name="Al-Hikmah",
            npwp="01.234.567.8-901.000",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # Schools in Foundation 1
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Al-Hikmah 1",
            npsn="10000001",
            level=School.LEVEL_SD,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Al-Hikmah 1",
            npsn="10000002",
            level=School.LEVEL_SMP,
        )

        # Foundation 2 (for cross-tenant tests)
        self.foundation_2 = Foundation.objects.create(
            legal_name="Yayasan Darul Ulum",
            brand_name="Darul Ulum",
            status=Foundation.STATUS_ACTIVE,
        )
        self.school_f2 = School.all_tenants.create(
            foundation_id=self.foundation_2.id,
            name="SMA Darul Ulum",
            npsn="20000001",
            level=School.LEVEL_SMA,
        )

        # Users
        self.person_admin = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Admin Foundation",
        )
        self.user_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="admin@alhikmah.sch.id",
            full_name="Admin Foundation",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id,
            user=self.user_admin,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # School A Admin
        self.person_school_a = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Admin SD",
        )
        self.user_school_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6282222222222",
            email="adminsd@alhikmah.sch.id",
            full_name="Admin SD",
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id,
            user=self.user_school_a,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )

        # Devices
        self.device_a = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            device_code="GATE-SD-01",
            name="Gerbang Masuk SD",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            location="Pos Satpam Depan",
            status=DeviceStatus.OFFLINE,
        )

        self.device_b = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            device_code="GATE-SMP-01",
            name="Gerbang Masuk SMP",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            location="Gerbang Utama SMP",
            status=DeviceStatus.OFFLINE,
        )

        self.client = APIClient()

    def test_device_creation_and_fields(self):
        """Assert device models persist correctly with proper tenancy and class."""
        self.assertEqual(self.device_a.device_class, DeviceClass.GATE_READER)
        self.assertEqual(self.device_a.status, DeviceStatus.OFFLINE)
        self.assertEqual(self.device_a.school, self.school_a)
        self.assertEqual(self.device_a.foundation_id, self.foundation.id)

    def test_school_admin_cross_school_device_isolation_404(self):
        """School A admin receives 404 when accessing School B's device."""
        self.client.force_authenticate(user=self.user_school_a)

        # School A device -> 200 OK
        resp_a = self.client.get(f'/api/v1/devices/{self.device_a.id}/')
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_a.data['device_code'], 'GATE-SD-01')

        # School B device -> 404 NOT FOUND (spec/02 §8.1 cross-school isolation)
        resp_b = self.client.get(f'/api/v1/devices/{self.device_b.id}/')
        self.assertEqual(resp_b.status_code, status.HTTP_404_NOT_FOUND)

    def test_device_heartbeat_updates_state(self):
        """Device heartbeat updates status, metrics, and last_heartbeat_at (HW-007)."""
        self.client.force_authenticate(user=self.user_admin)

        heartbeat_payload = {
            'status': 'ONLINE',
            'firmware_version': 'v1.4.2-desfire',
            'today_event_count': 142,
            'ip_address': '192.168.1.50',
            'metrics': {
                'queue_depth': 0,
                'uptime_seconds': 86400,
                'disk_free_mb': 12000,
            }
        }
        resp = self.client.post(
            f'/api/v1/devices/{self.device_a.id}/heartbeat/',
            data=heartbeat_payload,
            format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'ACK')

        self.device_a.refresh_from_db()
        self.assertEqual(self.device_a.status, DeviceStatus.ONLINE)
        self.assertEqual(self.device_a.firmware_version, 'v1.4.2-desfire')
        self.assertEqual(self.device_a.today_event_count, 142)
        self.assertEqual(str(self.device_a.ip_address), '192.168.1.50')
        self.assertIsNotNone(self.device_a.last_heartbeat_at)
        self.assertEqual(self.device_a.config['latest_metrics']['queue_depth'], 0)

    def test_retire_device_action(self):
        """Retiring a device transitions status to RETIRED (HW-015)."""
        self.client.force_authenticate(user=self.user_admin)

        resp = self.client.post(f'/api/v1/devices/{self.device_a.id}/retire/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'RETIRED')

        self.device_a.refresh_from_db()
        self.assertEqual(self.device_a.status, DeviceStatus.RETIRED)

    def test_cross_tenant_device_isolation(self):
        """Cross-tenant device query returns 404."""
        device_f2 = Device.objects.create(
            foundation_id=self.foundation_2.id,
            school=self.school_f2,
            device_code="GATE-SMA-F2",
            name="Gerbang SMA F2",
            device_class=DeviceClass.GATE_READER,
            status=DeviceStatus.ONLINE,
        )

        self.client.force_authenticate(user=self.user_admin)
        resp = self.client.get(f'/api/v1/devices/{device_f2.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

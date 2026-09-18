import datetime
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.attendance.models import (
    AttendanceDay,
    AttendanceRule,
    AttendanceSource,
    AttendanceStatus,
    Credential,
    CredentialType,
    GateDirection,
    GateEvent,
    GateEventStatus,
    GateMethod,
)
from apps.attendance.services import (
    get_device_sync_payload,
    get_live_gate_feed,
    ingest_gate_events,
    issue_credential,
    manual_gate_checkin,
    revoke_credential,
)
from apps.core.models import AuditEvent, DomainEvent
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, Student, User
from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, SCOPE_FOUNDATION, SCOPE_SCHOOL
from educore.middleware.tenancy import set_current_foundation_id


class GateConsoleAndSyncTests(TestCase):
    """
    Test suite for:
    - ATT-013: Live gate console cursor polling (3-second interval, ARC-015)
    - ATT-009: Real-time alert surfacing on gate console for rejected scans
    - ATT-013: Manual check-in for forgotten cards in <=3 taps
    - spec/12 §3, §7: Edge device incremental delta sync (roster, credentials, rules)
    - 3-Layer Tenancy & Cross-School 404 Isolation
    """

    def setUp(self):
        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.234.567.8-222.000",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # School A and School B
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="50000001",
            level=School.LEVEL_SMA,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Harapan Bangsa",
            npsn="50000002",
            level=School.LEVEL_SMP,
        )

        # Attendance rules for School A
        self.rule_a = AttendanceRule.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            late_after_time=datetime.time(7, 15, 0),
            absent_cutoff_time=datetime.time(9, 0, 0),
            debounce_seconds=120,
        )

        # Hardware Devices for School A
        self.device_gate = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="Gerbang Utama Masuk",
            device_code="GT-MASUK-01",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            status=DeviceStatus.ONLINE,
        )
        self.device_kiosk = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="Kiosk Pos Satpam",
            device_code="KSK-SATPAM-01",
            device_class=DeviceClass.KIOSK,
            direction=DeviceDirection.BIDIRECTIONAL,
            status=DeviceStatus.ONLINE,
        )

        # Students in School A
        self.person_student_1 = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Cut Nyak Dien",
            nik="3171012345678920",
        )
        self.student_1 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_student_1,
            nisn="0012345690",
            nis="2026-SMA-010",
            status=Student.STATUS_ACTIVE,
        )
        self.card_1 = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student_1,
            type=CredentialType.RFID,
            uid="RFID-CUT-01",
            card_number="CARD-010",
        )

        self.person_student_2 = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Teuku Umar",
            nik="3171012345678921",
        )
        self.student_2 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_student_2,
            nisn="0012345691",
            nis="2026-SMA-011",
            status=Student.STATUS_ACTIVE,
        )
        self.card_2 = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student_2,
            type=CredentialType.RFID,
            uid="RFID-TEUKU-02",
            card_number="CARD-011",
        )

        # Student in School B
        self.person_student_b = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Pangeran Diponegoro",
            nik="3171012345678922",
        )
        self.student_b = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            person=self.person_student_b,
            nisn="0012345692",
            nis="2026-SMP-010",
            status=Student.STATUS_ACTIVE,
        )

        # Users and Roles
        self.user_admin_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000010",
            email="admin.a@harapanbangsa.sch.id",
            full_name="Staff Gerbang SMA",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_admin_a,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )

        self.user_admin_b = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000011",
            email="admin.b@harapanbangsa.sch.id",
            full_name="Staff Gerbang SMP",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_admin_b,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_b.id,
        )

        self.client = APIClient()

    def test_live_gate_feed_cursor_polling(self):
        """ATT-013, ARC-015: 3-second cursor polling fetches new scans incrementally with next_cursor."""
        today = timezone.localdate()
        time_1 = timezone.make_aware(datetime.datetime.combine(today, datetime.time(6, 55, 0)))
        time_2 = timezone.make_aware(datetime.datetime.combine(today, datetime.time(6, 56, 0)))

        # 1. Initial ingestion of 1 scan
        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_gate.id,
                "occurred_at": time_1.isoformat(),
                "raw_uid": self.card_1.uid,
                "direction": "IN",
            }],
        )

        # Initial poll without cursor
        feed_1 = get_live_gate_feed(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
        )
        self.assertEqual(len(feed_1['events']), 1)
        self.assertEqual(feed_1['events'][0]['student']['nis'], self.student_1.nis)
        self.assertEqual(feed_1['events'][0]['student']['derived_status'], AttendanceStatus.HADIR)
        self.assertEqual(len(feed_1['devices_summary']), 2)
        cursor_1 = feed_1['next_cursor']
        self.assertIsNotNone(cursor_1)

        # 2. Poll immediately with cursor_1 (no new events yet)
        feed_empty = get_live_gate_feed(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            since=cursor_1,
        )
        self.assertEqual(len(feed_empty['events']), 0)

        # 3. New scan arrives 3 seconds later
        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_gate.id,
                "occurred_at": time_2.isoformat(),
                "raw_uid": self.card_2.uid,
                "direction": "IN",
            }],
        )

        # Poll with cursor_1 -> fetches only the newly arrived event
        feed_2 = get_live_gate_feed(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            since=cursor_1,
        )
        self.assertEqual(len(feed_2['events']), 1)
        self.assertEqual(feed_2['events'][0]['student']['nis'], self.student_2.nis)
        self.assertGreater(feed_2['next_cursor'], cursor_1)

    def test_live_gate_rejected_card_alert(self):
        """ATT-009: Rejected unknown card is categorized under alerts on the live console."""
        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_gate.id,
                "occurred_at": timezone.now().isoformat(),
                "raw_uid": "INVALID-CARD-666",
                "direction": "IN",
            }],
        )

        feed = get_live_gate_feed(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
        )
        self.assertEqual(len(feed['alerts']), 1)
        alert = feed['alerts'][0]
        self.assertEqual(alert['status'], GateEventStatus.REJECTED)
        self.assertIn("tidak ditemukan", alert['reject_reason'])

    def test_manual_checkin_flow(self):
        """ATT-013: Staff manual check-in for a student without card in <=3 taps."""
        today = timezone.localdate()
        checkin_time = timezone.make_aware(datetime.datetime.combine(today, datetime.time(7, 5, 0)))

        result = manual_gate_checkin(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            student_id=self.student_1.id,
            direction="IN",
            occurred_at=checkin_time,
            reason="Kartu tertinggal di rumah",
            user=self.user_admin_a,
        )

        gate_event = result['gate_event']
        att_day = result['attendance_day']

        # Verify GateEvent
        self.assertEqual(gate_event.method, GateMethod.MANUAL)
        self.assertEqual(gate_event.status, GateEventStatus.ACCEPTED)
        self.assertEqual(gate_event.student, self.student_1)
        self.assertEqual(gate_event.direction, GateDirection.IN)

        # Verify AttendanceDay
        self.assertEqual(att_day.status, AttendanceStatus.HADIR)
        self.assertEqual(att_day.source, AttendanceSource.MANUAL)
        self.assertEqual(att_day.note, "Kartu tertinggal di rumah")

        # Verify AuditEvent
        audit_entry = AuditEvent.objects.filter(
            foundation_id=self.foundation.id,
            action='attendance.manual.checkin',
            entity_id=str(gate_event.id)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.diff['reason'], "Kartu tertinggal di rumah")

        # Verify DomainEvent
        domain_entry = DomainEvent.objects.filter(
            foundation_id=self.foundation.id,
            name='attendance.gate.scanned'
        ).latest('occurred_at')
        self.assertEqual(domain_entry.payload['method'], GateMethod.MANUAL)
        self.assertEqual(domain_entry.payload['student_id'], str(self.student_1.id))

    def test_device_sync_payload_export(self):
        """spec/12 §3, §7: Edge delta sync produces active roster, credentials, and timing rules."""
        sync_data = get_device_sync_payload(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
        )

        # 1. Roster
        self.assertEqual(len(sync_data['roster_delta']), 2)
        roster_nises = [s['nis'] for s in sync_data['roster_delta']]
        self.assertIn(self.student_1.nis, roster_nises)
        self.assertIn(self.student_2.nis, roster_nises)

        # 2. Credentials
        self.assertEqual(len(sync_data['credentials_delta']), 2)
        cred_uids = [c['uid'] for c in sync_data['credentials_delta']]
        self.assertIn(self.card_1.uid, cred_uids)
        self.assertIn(self.card_2.uid, cred_uids)

        # 3. Timing rules
        self.assertEqual(sync_data['rules_delta']['late_after_time'], '07:15:00')
        self.assertEqual(sync_data['rules_delta']['debounce_seconds'], 120)
        self.assertIsNotNone(sync_data['next_cursor'])

    def test_api_endpoints_live_and_manual_checkin(self):
        """API endpoints: GET /api/v1/gate/live and POST /api/v1/attendance/manual."""
        self.client.force_authenticate(user=self.user_admin_a)

        # 1. POST /api/v1/attendance/manual
        payload = {
            "student_id": self.student_1.id,
            "direction": "IN",
            "occurred_at": timezone.now().isoformat(),
            "reason": "Lupa bawa lanyard kartu",
        }
        res_post = self.client.post('/api/v1/attendance/manual/', payload, format='json')
        self.assertEqual(res_post.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_post.data['gate_event']['method'], GateMethod.MANUAL)
        self.assertEqual(res_post.data['attendance_day']['source'], AttendanceSource.MANUAL)

        # 2. GET /api/v1/gate/live?school_id=...
        res_live = self.client.get(f'/api/v1/gate/live?school_id={self.school_a.id}')
        self.assertEqual(res_live.status_code, status.HTTP_200_OK)
        self.assertIn('events', res_live.data)
        self.assertIn('alerts', res_live.data)
        self.assertIn('devices_summary', res_live.data)
        self.assertIn('next_cursor', res_live.data)
        self.assertTrue(any(e['student'] and e['student']['nis'] == self.student_1.nis for e in res_live.data['events']))

        # 3. GET /api/v1/device/sync?school_id=...
        res_sync = self.client.get(f'/api/v1/device/sync?school_id={self.school_a.id}')
        self.assertEqual(res_sync.status_code, status.HTTP_200_OK)
        self.assertIn('roster_delta', res_sync.data)
        self.assertIn('credentials_delta', res_sync.data)
        self.assertIn('rules_delta', res_sync.data)

    def test_cross_school_404_isolation(self):
        """Layer 3: School A admin receives 403/404 when attempting to access School B endpoints."""
        self.client.force_authenticate(user=self.user_admin_a)

        # School A admin attempts to poll School B live feed (IAM-012)
        res_live_b = self.client.get(f'/api/v1/gate/live?school_id={self.school_b.id}')
        self.assertEqual(res_live_b.status_code, status.HTTP_403_FORBIDDEN)

        # School A admin attempts to manual check-in School B student (object isolation returns 404)
        res_manual_b = self.client.post('/api/v1/attendance/manual/', {
            "student_id": self.student_b.id,
            "direction": "IN",
            "reason": "Unauthorized manual checkin",
        }, format='json')
        self.assertEqual(res_manual_b.status_code, status.HTTP_404_NOT_FOUND)

        # School A admin attempts to sync School B device data (IAM-012)
        res_sync_b = self.client.get(f'/api/v1/device/sync?school_id={self.school_b.id}')
        self.assertEqual(res_sync_b.status_code, status.HTTP_403_FORBIDDEN)


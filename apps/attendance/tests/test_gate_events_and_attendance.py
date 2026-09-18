import datetime
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
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
    CredentialStatus,
    CredentialType,
    GateDirection,
    GateEvent,
    GateEventStatus,
    GateMethod,
)
from apps.attendance.services import (
    get_or_flip_attendance_day_to_manual,
    ingest_gate_events,
    issue_credential,
    override_attendance_day,
    revoke_credential,
)
from apps.core.models import AuditEvent, DomainEvent
from apps.hardware.models import Device, DeviceClass, DeviceDirection
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, Student, User
from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, SCOPE_FOUNDATION, SCOPE_SCHOOL
from educore.middleware.tenancy import set_current_foundation_id


class GateEventsAndAttendanceTests(TestCase):
    """
    Test suite for:
    - HW-005: Idempotent batch ingestion on event_uuid
    - ATT-007: Debouncing of duplicate scans within window
    - ATT-008: Direction inference (fixed vs alternating bidirectional)
    - ATT-009: Rejected scan recording for unknown/revoked credentials
    - ATT-001: Automatic daily attendance derivation (HADIR vs TERLAMBAT)
    - ATT-003: Staff override with mandatory note and audit logging
    - ATT-012: Offline replayed scans retention
    - 3-Layer Tenancy and Cross-School 404 Isolation
    """

    def setUp(self):
        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa",
            brand_name="Bina Bangsa",
            npwp="01.234.567.8-111.000",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # School A and School B
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Bina Bangsa",
            npsn="30000001",
            level=School.LEVEL_SMA,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Bina Bangsa",
            npsn="30000002",
            level=School.LEVEL_SMP,
        )

        # Foundation 2 (for cross-tenant tests)
        self.foundation_2 = Foundation.objects.create(
            legal_name="Yayasan Insan Cita",
            brand_name="Insan Cita",
            status=Foundation.STATUS_ACTIVE,
        )
        self.school_f2 = School.all_tenants.create(
            foundation_id=self.foundation_2.id,
            name="SMA Insan Cita",
            npsn="40000001",
            level=School.LEVEL_SMA,
        )

        # Attendance Rules for School A
        self.rule_a = AttendanceRule.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            late_after_time=datetime.time(7, 15, 0),
            absent_cutoff_time=datetime.time(9, 0, 0),
            debounce_seconds=120,
        )

        # Devices in School A
        self.device_in = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="Turnstile Barat - Masuk",
            device_code="GT-IN-01",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            ip_address="192.168.1.101",
        )
        self.device_out = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="Turnstile Barat - Keluar",
            device_code="GT-OUT-01",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.OUT,
            ip_address="192.168.1.102",
        )
        self.device_bidi = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            name="Pedestrian Gate Timur",
            device_code="GT-BIDI-01",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.BIDIRECTIONAL,
            ip_address="192.168.1.103",
        )

        # Student in School A
        self.person_student = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Raden Saleh",
            nik="3171012345678910",
        )
        self.student = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_student,
            nisn="0012345688",
            nis="2026-SMA-001",
            status=Student.STATUS_ACTIVE,
        )

        # Card for Student
        self.card = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student,
            type=CredentialType.RFID,
            uid="RFID-CARD-1001",
            card_number="CARD-001",
        )

        # Student in School B
        self.person_student_b = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Dewi Sartika",
            nik="3171012345678911",
        )
        self.student_b = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            person=self.person_student_b,
            nisn="0012345689",
            nis="2026-SMP-001",
            status=Student.STATUS_ACTIVE,
        )
        self.card_b = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student_b,
            type=CredentialType.RFID,
            uid="RFID-CARD-2001",
            card_number="CARD-002",
        )

        # Users and Roles
        self.user_admin_a = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000001",
            email="admin.a@binabangsa.sch.id",
            full_name="Admin SMA",
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
            phone_e164="+6281200000002",
            email="admin.b@binabangsa.sch.id",
            full_name="Admin SMP",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_admin_b,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_b.id,
        )

        self.user_fnd_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000099",
            email="fnd.admin@binabangsa.sch.id",
            full_name="Yayasan Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_fnd_admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.client = APIClient()

    def test_idempotent_batch_ingestion(self):
        """HW-005: Replayed batches with same event_uuid must skip duplicates and remain idempotent."""
        event_uuid_1 = uuid.uuid4()
        event_uuid_2 = uuid.uuid4()
        occurred_time = timezone.now().replace(hour=6, minute=50, second=0, microsecond=0)

        batch_payload = [
            {
                "event_uuid": str(event_uuid_1),
                "device_id": self.device_in.id,
                "occurred_at": occurred_time.isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
                "method": "RFID",
            },
            {
                "event_uuid": str(event_uuid_2),
                "device_id": self.device_in.id,
                "occurred_at": (occurred_time + timedelta(seconds=1)).isoformat(),
                "raw_uid": "UNKNOWN-CARD-999",
                "direction": "IN",
                "method": "RFID",
            },
        ]

        # First ingestion run
        result_1 = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=batch_payload,
            user=self.user_admin_a,
        )
        self.assertEqual(result_1['total'], 2)
        self.assertEqual(result_1['accepted'], 1)
        self.assertEqual(result_1['rejected'], 1)
        self.assertEqual(result_1['duplicates_skipped'], 0)
        self.assertEqual(GateEvent.objects.filter(foundation_id=self.foundation.id).count(), 2)

        # Second ingestion run (same payload)
        result_2 = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=batch_payload,
            user=self.user_admin_a,
        )
        self.assertEqual(result_2['total'], 2)
        self.assertEqual(result_2['duplicates_skipped'], 2)
        self.assertEqual(result_2['accepted'], 0)
        self.assertEqual(result_2['rejected'], 0)
        # Database count remains strictly 2 (0 duplicates created)
        self.assertEqual(GateEvent.objects.filter(foundation_id=self.foundation.id).count(), 2)

    def test_debouncing_duplicate_scans(self):
        """ATT-007: Multiple taps within debounce window are recorded with is_duplicate_scan=True but do not re-notify."""
        base_time = timezone.now().replace(hour=7, minute=0, second=0, microsecond=0)
        taps = [
            {
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": (base_time + timedelta(seconds=0)).isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
            },
            {
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": (base_time + timedelta(seconds=10)).isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
            },
            {
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": (base_time + timedelta(seconds=25)).isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
            },
        ]

        result = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=taps,
            user=self.user_admin_a,
        )

        self.assertEqual(result['accepted'], 3)
        self.assertEqual(result['debounced'], 2)

        # Check GateEvent records
        events = GateEvent.objects.filter(foundation_id=self.foundation.id, student=self.student).order_by('occurred_at')
        self.assertEqual(events.count(), 3)
        self.assertFalse(events[0].is_duplicate_scan)
        self.assertTrue(events[1].is_duplicate_scan)
        self.assertTrue(events[2].is_duplicate_scan)

        # Check DomainEvents: exactly ONE notification domain event dispatched
        domain_events = DomainEvent.objects.filter(
            foundation_id=self.foundation.id,
            name='attendance.gate.scanned'
        )
        self.assertEqual(domain_events.count(), 1)
        self.assertEqual(domain_events[0].payload['event_uuid'], str(events[0].event_uuid))

    def test_bidirectional_device_direction_inference(self):
        """ATT-008: Bidirectional gate alternates IN and OUT based on student's last scan."""
        day = timezone.now().date()
        time_1 = timezone.make_aware(datetime.datetime.combine(day, datetime.time(7, 0, 0)))
        time_2 = timezone.make_aware(datetime.datetime.combine(day, datetime.time(14, 30, 0)))
        time_3 = timezone.make_aware(datetime.datetime.combine(day, datetime.time(15, 0, 0)))

        # 1. First scan on bidirectional device -> Inferred IN
        result_1 = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_bidi.id,
                "occurred_at": time_1.isoformat(),
                "raw_uid": self.card.uid,
                # direction omitted to trigger inference
            }],
        )
        evt_1 = result_1['events'][0]
        self.assertEqual(evt_1.direction, GateDirection.IN)

        # 2. Second scan later that day -> Inferred OUT
        result_2 = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_bidi.id,
                "occurred_at": time_2.isoformat(),
                "raw_uid": self.card.uid,
            }],
        )
        evt_2 = result_2['events'][0]
        self.assertEqual(evt_2.direction, GateDirection.OUT)

        # 3. Third scan later (e.g. re-entry for extracurricular) -> Inferred IN
        result_3 = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_bidi.id,
                "occurred_at": time_3.isoformat(),
                "raw_uid": self.card.uid,
            }],
        )
        evt_3 = result_3['events'][0]
        self.assertEqual(evt_3.direction, GateDirection.IN)

    def test_daily_attendance_status_derivation(self):
        """ATT-001: First IN before late_after (07:15) -> HADIR; after -> TERLAMBAT."""
        day = timezone.now().date()
        on_time_scan = timezone.make_aware(datetime.datetime.combine(day, datetime.time(7, 10, 0)))

        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": on_time_scan.isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
            }],
        )

        att_day = AttendanceDay.objects.get(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=day,
        )
        self.assertEqual(att_day.status, AttendanceStatus.HADIR)
        self.assertEqual(att_day.source, AttendanceSource.GATE)
        self.assertEqual(att_day.first_in_at, on_time_scan)

        # Test Late derivation for next day
        next_day = day + timedelta(days=1)
        late_scan = timezone.make_aware(datetime.datetime.combine(next_day, datetime.time(7, 25, 0)))
        out_scan = timezone.make_aware(datetime.datetime.combine(next_day, datetime.time(15, 0, 0)))

        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[
                {
                    "event_uuid": str(uuid.uuid4()),
                    "device_id": self.device_in.id,
                    "occurred_at": late_scan.isoformat(),
                    "raw_uid": self.card.uid,
                    "direction": "IN",
                },
                {
                    "event_uuid": str(uuid.uuid4()),
                    "device_id": self.device_out.id,
                    "occurred_at": out_scan.isoformat(),
                    "raw_uid": self.card.uid,
                    "direction": "OUT",
                }
            ],
        )

        att_day_late = AttendanceDay.objects.get(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=next_day,
        )
        self.assertEqual(att_day_late.status, AttendanceStatus.TERLAMBAT)
        self.assertEqual(att_day_late.first_in_at, late_scan)
        self.assertEqual(att_day_late.last_out_at, out_scan)

    def test_rejected_scan_unknown_or_revoked_card(self):
        """ATT-009: Unknown or revoked cards recorded as status=REJECTED with reason."""
        # 1. Unknown card
        result = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": timezone.now().isoformat(),
                "raw_uid": "NO-SUCH-CARD-XYZ",
            }],
        )
        self.assertEqual(result['rejected'], 1)
        event = GateEvent.objects.get(raw_uid="NO-SUCH-CARD-XYZ")
        self.assertEqual(event.status, GateEventStatus.REJECTED)
        self.assertIn("tidak ditemukan", event.reject_reason)

        # 2. Revoked card
        revoke_credential(
            credential=self.card,
            reason="Kartu hilang di kantin",
            user=self.user_admin_a,
        )

        result_revoked = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": timezone.now().isoformat(),
                "raw_uid": self.card.uid,
            }],
        )
        self.assertEqual(result_revoked['rejected'], 1)
        revoked_evt = GateEvent.objects.filter(raw_uid=self.card.uid).latest('created_at')
        self.assertEqual(revoked_evt.status, GateEventStatus.REJECTED)
        self.assertIn("Kartu hilang di kantin", revoked_evt.reject_reason)

    def test_staff_override_with_note_and_audit(self):
        """ATT-003: Staff override requires mandatory note, retains original status, and logs audit."""
        day = timezone.now().date()
        att_day = AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=day,
            status=AttendanceStatus.TERLAMBAT,
            source=AttendanceSource.GATE,
        )

        # Empty note should fail
        with self.assertRaises(ValidationError):
            override_attendance_day(
                foundation_id=str(self.foundation.id),
                attendance_day=att_day,
                new_status=AttendanceStatus.DISPEN,
                note="",
                user=self.user_admin_a,
            )

        # Valid override
        updated = override_attendance_day(
            foundation_id=str(self.foundation.id),
            attendance_day=att_day,
            new_status=AttendanceStatus.DISPEN,
            note="Mewakili sekolah lomba olimpiade sains",
            user=self.user_admin_a,
        )

        self.assertEqual(updated.status, AttendanceStatus.DISPEN)
        self.assertEqual(updated.original_status, AttendanceStatus.TERLAMBAT)
        self.assertTrue(updated.is_override)
        self.assertEqual(updated.note, "Mewakili sekolah lomba olimpiade sains")

        # Verify AuditEvent
        audit_event = AuditEvent.objects.filter(
            foundation_id=self.foundation.id,
            action='attendance.day.overridden',
            entity_id=str(att_day.id),
        ).first()
        self.assertIsNotNone(audit_event)
        self.assertEqual(audit_event.diff['old_status'], AttendanceStatus.TERLAMBAT)
        self.assertEqual(audit_event.diff['new_status'], AttendanceStatus.DISPEN)

        # Verify subsequent scan does NOT overwrite staff override
        late_scan = timezone.make_aware(datetime.datetime.combine(day, datetime.time(10, 0, 0)))
        ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(uuid.uuid4()),
                "device_id": self.device_in.id,
                "occurred_at": late_scan.isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
            }],
        )
        updated.refresh_from_db()
        self.assertEqual(updated.status, AttendanceStatus.DISPEN)
        self.assertTrue(updated.is_override)

    def test_get_or_flip_attendance_day_to_manual_creates_with_created_by(self):
        """Shared by approve_absence_request and the clinic SAKIT override: a brand-new
        AttendanceDay must record created_by (not just updated_by via override_attendance_day
        immediately after), so data-lineage queries can still tell who created the row."""
        day = timezone.now().date()

        att_day = get_or_flip_attendance_day_to_manual(
            foundation_id=str(self.foundation.id),
            school=self.school_a,
            student=self.student,
            date=day,
            actor_id=str(self.user_admin_a.id),
        )

        self.assertEqual(att_day.source, AttendanceSource.MANUAL)
        self.assertEqual(att_day.created_by, str(self.user_admin_a.id))

    def test_get_or_flip_attendance_day_to_manual_flips_and_audits(self):
        """A pre-existing GATE-sourced day must flip to MANUAL with its own audit event —
        override_attendance_day itself never touches `source`, so this is the only place
        the source change is recorded."""
        day = timezone.now().date()
        att_day = AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=day,
            status=AttendanceStatus.HADIR,
            source=AttendanceSource.GATE,
        )

        flipped = get_or_flip_attendance_day_to_manual(
            foundation_id=str(self.foundation.id),
            school=self.school_a,
            student=self.student,
            date=day,
            actor_id=str(self.user_admin_a.id),
        )

        self.assertEqual(flipped.id, att_day.id)
        self.assertEqual(flipped.source, AttendanceSource.MANUAL)

        audit_event = AuditEvent.objects.filter(
            foundation_id=self.foundation.id,
            action='attendance.day.source_flipped_to_manual',
            entity_id=str(att_day.id),
        ).first()
        self.assertIsNotNone(audit_event)
        self.assertEqual(audit_event.diff['old_source'], AttendanceSource.GATE)
        self.assertEqual(audit_event.diff['new_source'], AttendanceSource.MANUAL)

    def test_get_or_flip_attendance_day_to_manual_no_op_when_already_manual(self):
        """An already-MANUAL day must not produce a spurious source-flip audit event."""
        day = timezone.now().date()
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=day,
            status=AttendanceStatus.SAKIT,
            source=AttendanceSource.MANUAL,
        )

        get_or_flip_attendance_day_to_manual(
            foundation_id=str(self.foundation.id),
            school=self.school_a,
            student=self.student,
            date=day,
            actor_id=str(self.user_admin_a.id),
        )

        self.assertFalse(
            AuditEvent.objects.filter(
                foundation_id=self.foundation.id,
                action='attendance.day.source_flipped_to_manual',
            ).exists()
        )

    def test_offline_buffered_replayed_scans(self):
        """ATT-012: Offline replayed scans maintain original timestamps and replayed=True."""
        historic_time = timezone.now() - timedelta(hours=3)
        event_uuid = uuid.uuid4()

        result = ingest_gate_events(
            foundation_id=str(self.foundation.id),
            school_id=str(self.school_a.id),
            events_data=[{
                "event_uuid": str(event_uuid),
                "device_id": self.device_in.id,
                "occurred_at": historic_time.isoformat(),
                "raw_uid": self.card.uid,
                "direction": "IN",
                "replayed": True,
            }],
        )
        self.assertEqual(result['accepted'], 1)
        event = GateEvent.objects.get(event_uuid=event_uuid)
        self.assertTrue(event.replayed)
        self.assertEqual(event.occurred_at.date(), historic_time.date())

    def test_api_batch_ingest_and_query_endpoints(self):
        """API test: POST /api/v1/gate/events/ and GET /api/v1/gate/events/."""
        self.client.force_authenticate(user=self.user_admin_a)
        event_uuid = str(uuid.uuid4())
        scan_time = timezone.now().replace(hour=7, minute=5, second=0).isoformat()

        payload = {
            "school_id": self.school_a.id,
            "events": [
                {
                    "event_uuid": event_uuid,
                    "device_id": self.device_in.id,
                    "occurred_at": scan_time,
                    "raw_uid": self.card.uid,
                    "direction": "IN",
                    "method": "RFID",
                }
            ]
        }

        # 1. Ingest
        response = self.client.post('/api/v1/gate/events/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['accepted'], 1)
        self.assertEqual(len(response.data['events']), 1)

        # 2. Query
        get_response = self.client.get(f'/api/v1/gate/events/?school_id={self.school_a.id}')
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        results = get_response.data.get('results', [])
        self.assertTrue(any(e['event_uuid'] == event_uuid for e in results))

    def test_api_daily_attendance_override_and_patch(self):
        """API test: POST /api/v1/attendance/daily/{id}/override/ and PATCH."""
        self.client.force_authenticate(user=self.user_admin_a)
        day = timezone.now().date()
        att_day = AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            student=self.student,
            date=day,
            status=AttendanceStatus.ALPA,
            source=AttendanceSource.SYSTEM,
        )

        override_payload = {
            "status": "SAKIT",
            "note": "Surat keterangan dokter Puskesmas terlampir.",
        }

        response = self.client.post(
            f'/api/v1/attendance/daily/{att_day.id}/override/',
            override_payload,
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], AttendanceStatus.SAKIT)
        self.assertTrue(response.data['is_override'])
        self.assertEqual(response.data['original_status'], AttendanceStatus.ALPA)

    def test_cross_school_and_cross_tenant_isolation(self):
        """Layer 3: School A admin receives 404 for School B records, and Foundation 2 cannot see Foundation 1."""
        day = timezone.now().date()
        att_day_b = AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            student=self.student_b,
            date=day,
            status=AttendanceStatus.HADIR,
            source=AttendanceSource.GATE,
        )

        # School A admin attempts to access School B's attendance day record
        self.client.force_authenticate(user=self.user_admin_a)
        res_get = self.client.get(f'/api/v1/attendance/daily/{att_day_b.id}/')
        self.assertEqual(res_get.status_code, status.HTTP_404_NOT_FOUND)

        # School A admin attempts to override School B's attendance day record
        res_override = self.client.post(
            f'/api/v1/attendance/daily/{att_day_b.id}/override/',
            {"status": "IZIN", "note": "Hacking attempt"},
            format='json'
        )
        self.assertEqual(res_override.status_code, status.HTTP_404_NOT_FOUND)

        # School A admin attempts to batch ingest for School B
        res_ingest = self.client.post(
            '/api/v1/gate/events/',
            {
                "school_id": self.school_b.id,
                "events": [{
                    "event_uuid": str(uuid.uuid4()),
                    "device_id": self.device_in.id,
                    "occurred_at": timezone.now().isoformat(),
                    "raw_uid": self.card_b.uid,
                }]
            },
            format='json'
        )
        self.assertEqual(res_ingest.status_code, status.HTTP_404_NOT_FOUND)

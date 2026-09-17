"""DEPARTURE gate scans must dispatch a notification per linked guardian,
mirroring the existing ARRIVAL path (spec/08 PAR-004, spec/05 §4 ATT-006)."""
import uuid
from django.test import TestCase
from django.utils import timezone
from apps.attendance.models import Credential, CredentialType
from apps.attendance.services import ingest_gate_events, issue_credential
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.notifications.models import IntentStatus, NotificationIntent
from apps.notifications.providers import MockPushProvider, MockWhatsAppProvider, register_provider
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class DepartureNotificationTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Islam Terpadu Al-Qalam",
            brand_name="Al-Qalam",
            npwp="01.234.567.8-111.001",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD IT Al-Qalam",
            npsn="20300002",
            level=School.LEVEL_SD,
        )
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010003",
            full_name="Umar bin Khattab",
            gender="M",
            dob="2015-06-11",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nisn="0012345679",
            nis="24002",
        )
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010004",
            full_name="Khalid bin Walid",
            gender="M",
            dob="1985-02-02",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281399887777",
            email="khalid@example.sch.id",
            full_name="Khalid bin Walid",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        self.link = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            can_pickup=True,
            is_primary=True,
        )
        self.device = Device.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            device_code="GATE-OUT-01",
            name="Turnstile Gerbang Keluar",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.OUT,
            status=DeviceStatus.ONLINE,
        )
        self.credential = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student,
            type=CredentialType.RFID,
            uid="E280116060000205",
        )
        # WHATSAPP is tried before PUSH in the default channel ladder (NTF-001) and the
        # process-global mock WhatsApp provider always succeeds; force it to fail here so
        # dispatch falls through to PUSH, letting us verify the PUSH data payload end-to-end
        # (spec/08 PAR-004) via Task 1's MockPushProvider variable capture.
        self.mock_whatsapp = MockWhatsAppProvider()
        self.mock_whatsapp.simulate_failure = True
        register_provider('WHATSAPP', self.mock_whatsapp)
        self.mock_push = MockPushProvider()
        register_provider('PUSH', self.mock_push)

    def test_gate_scan_out_produces_departure_notification(self):
        scan_time = timezone.now()
        events_payload = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000205',
            'occurred_at': scan_time.isoformat(),
            'method': 'RFID',
        }]

        with tenant_context(self.foundation.id):
            result = ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=events_payload,
            )
            self.assertEqual(result['accepted'], 1)

            intent = NotificationIntent.objects.filter(
                foundation_id=self.foundation.id,
                recipient_phone="+6281399887777",
                category="DEPARTURE",
            ).first()
            self.assertIsNotNone(intent)
            self.assertEqual(intent.status, IntentStatus.DISPATCHED)
            self.assertEqual(intent.payload['type'], 'DEPARTURE')
            self.assertEqual(intent.payload['student_id'], self.student.id)
            self.assertEqual(intent.payload['date'], scan_time.strftime('%Y-%m-%d'))

            self.assertEqual(len(self.mock_push.dispatched_messages), 1)
            self.assertEqual(
                self.mock_push.dispatched_messages[0]['variables']['type'], 'DEPARTURE',
            )
            self.assertEqual(
                self.mock_push.dispatched_messages[0]['variables']['student_id'], self.student.id,
            )

    def test_cross_foundation_isolation(self):
        """A DEPARTURE notification never leaks into another foundation's NotificationIntent set."""
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="09.876.543.2-000.000", address="Bandung",
        )
        scan_time = timezone.now()
        events_payload = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000205',
            'occurred_at': scan_time.isoformat(),
            'method': 'RFID',
        }]
        with tenant_context(self.foundation.id):
            ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=events_payload,
            )
        with tenant_context(other_foundation.id):
            leaked = NotificationIntent.objects.filter(category="DEPARTURE").exists()
            self.assertFalse(leaked)

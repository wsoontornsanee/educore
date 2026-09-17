import uuid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.attendance.models import Credential, CredentialType
from apps.attendance.services import ingest_gate_events, issue_credential
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.notifications.models import DeliveryStatus, IntentStatus, NotificationDelivery, NotificationIntent
from apps.notifications.providers import MockWhatsAppProvider, register_provider
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class WhatsAppArrivalMessagingIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Islam Terpadu Al-Qalam",
            brand_name="Al-Qalam",
            npwp="01.234.567.8-111.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD IT Al-Qalam",
            npsn="20300001",
            level=School.LEVEL_SD,
        )
        # Student person & record
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010001",
            full_name="Zaid bin Tsabit",
            gender="M",
            dob="2015-05-10",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nisn="0012345678",
            nis="24001",
        )
        # Guardian person, user & link
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010002",
            full_name="Abu Bakar",
            gender="M",
            dob="1985-01-01",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281399887766",
            email="abubakar@example.sch.id",
            full_name="Abu Bakar",
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
        # Gate device
        self.device = Device.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            device_code="GATE-IN-01",
            name="Turnstile Gerbang Utama",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            status=DeviceStatus.ONLINE,
        )
        # Issue RFID card
        self.credential = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student,
            type=CredentialType.RFID,
            uid="E280116060000204",
        )
        # Setup mock whatsapp provider
        self.mock_whatsapp = MockWhatsAppProvider()
        register_provider('WHATSAPP', self.mock_whatsapp)

    def test_gate_scan_produces_arrival_notification_within_5s(self):
        scan_time = timezone.now()
        events_payload = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000204',
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

            # Assert NotificationIntent created for the linked parent
            intent = NotificationIntent.objects.filter(
                foundation_id=self.foundation.id,
                recipient_phone="+6281399887766",
                category="ARRIVAL",
            ).first()
            self.assertIsNotNone(intent)
            self.assertEqual(intent.status, IntentStatus.DISPATCHED)
            self.assertEqual(intent.payload['type'], 'ARRIVAL')
            self.assertEqual(intent.payload['student_id'], self.student.id)

            # Assert Delivery row
            delivery = NotificationDelivery.objects.filter(intent=intent).first()
            self.assertIsNotNone(delivery)
            self.assertEqual(delivery.status, DeliveryStatus.SENT)
            self.assertIn('Zaid bin Tsabit', delivery.rendered_body)
            self.assertIn('SD IT Al-Qalam', delivery.rendered_body)
            self.assertIn('Turnstile Gerbang Utama', delivery.rendered_body)

            # Assert Mock WhatsApp received the message
            self.assertEqual(len(self.mock_whatsapp.dispatched_messages), 1)
            sent_msg = self.mock_whatsapp.dispatched_messages[0]
            self.assertEqual(sent_msg['recipient'], '+6281399887766')
            self.assertIn('Zaid bin Tsabit', sent_msg['body'])

    def test_duplicate_gate_scan_within_debounce_window_does_not_re_notify(self):
        scan_time = timezone.now()
        event_1 = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000204',
            'occurred_at': scan_time.isoformat(),
            'method': 'RFID',
        }]
        event_2_duplicate = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000204',
            'occurred_at': (scan_time + timezone.timedelta(seconds=15)).isoformat(),
            'method': 'RFID',
        }]

        with tenant_context(self.foundation.id):
            # First scan
            ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=event_1,
            )
            self.assertEqual(len(self.mock_whatsapp.dispatched_messages), 1)

            # Second scan (within 120s debounce window)
            res2 = ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=event_2_duplicate,
            )
            self.assertEqual(res2['debounced'], 1)
            # Must NOT re-notify per ATT-007
            self.assertEqual(len(self.mock_whatsapp.dispatched_messages), 1)

    def test_whatsapp_webhook_status_callback(self):
        with tenant_context(self.foundation.id):
            # Create a dispatched delivery
            intent = NotificationIntent.objects.create(
                foundation_id=self.foundation.id,
                recipient_phone="+6281399887766",
                category="ARRIVAL",
                template_key="attendance.arrival",
                status=IntentStatus.DISPATCHED,
            )
            delivery = NotificationDelivery.objects.create(
                foundation_id=self.foundation.id,
                intent=intent,
                channel="WHATSAPP",
                provider="whatsapp_cloud_api",
                provider_message_id="wamid.TEST_RECEIPT_123",
                recipient_target="+6281399887766",
                status=DeliveryStatus.SENT,
            )

        # Simulate WhatsApp Cloud API status webhook callback
        webhook_payload = {
            "statuses": [
                {
                    "id": "wamid.TEST_RECEIPT_123",
                    "status": "delivered",
                    "timestamp": "1726388400",
                    "recipient_id": "6281399887766",
                }
            ]
        }
        response = self.client.post('/api/v1/webhooks/whatsapp/status/', webhook_payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['updated'], 1)

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, DeliveryStatus.DELIVERED)
        self.assertIsNotNone(delivery.delivered_at)

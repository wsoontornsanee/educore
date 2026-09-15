import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import Foundation, School, User
from apps.notifications.models import (
    ChannelType,
    DeliveryStatus,
    IntentStatus,
    NotificationCategory,
    NotificationDelivery,
    NotificationIntent,
    NotificationPreference,
    NotificationPriority,
    NotificationTemplate,
)
from apps.notifications.providers import (
    MockPushProvider,
    MockWhatsAppProvider,
    register_provider,
)
from apps.notifications.services import (
    calculate_next_quiet_hours_end,
    dispatch_intent,
    is_in_quiet_hours,
    process_intent,
)
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class NotificationDispatchServicesTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia Nusantara",
            brand_name="Cendekia",
            npwp="01.234.567.8-999.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Cendekia",
            npsn="20200001",
            level=School.LEVEL_SMP,
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="parent@example.sch.id",
            full_name="Budi Pratama",
        )
        self.mock_whatsapp = MockWhatsAppProvider()
        self.mock_push = MockPushProvider()
        register_provider('WHATSAPP', self.mock_whatsapp)
        register_provider('PUSH', self.mock_push)

    def test_deduplication_prevents_duplicate_intents(self):
        with tenant_context(self.foundation.id):
            dedupe_key = "arrival:101:2026-09-15:201"
            intent1 = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.parent_user,
                recipient_phone="+6281234567890",
                category=NotificationCategory.ARRIVAL,
                template_key='attendance.arrival',
                payload={'student_name': 'Ali'},
                dedupe_key=dedupe_key,
            )
            intent2 = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.parent_user,
                recipient_phone="+6281234567890",
                category=NotificationCategory.ARRIVAL,
                template_key='attendance.arrival',
                payload={'student_name': 'Ali'},
                dedupe_key=dedupe_key,
            )
            self.assertEqual(intent1.id, intent2.id)
            self.assertEqual(NotificationIntent.objects.count(), 1)

    def test_quiet_hours_calculation_and_deferral(self):
        q_start = datetime.time(21, 0)
        q_end = datetime.time(6, 0)
        self.assertTrue(is_in_quiet_hours(datetime.time(22, 0), q_start, q_end))
        self.assertTrue(is_in_quiet_hours(datetime.time(5, 30), q_start, q_end))
        self.assertFalse(is_in_quiet_hours(datetime.time(7, 0), q_start, q_end))
        self.assertFalse(is_in_quiet_hours(datetime.time(12, 0), q_start, q_end))

        now_at_night = timezone.now().replace(hour=23, minute=30, second=0, microsecond=0)
        next_end = calculate_next_quiet_hours_end(now_at_night, q_end)
        self.assertEqual(next_end.hour, 6)
        self.assertEqual(next_end.minute, 0)
        self.assertTrue(next_end > now_at_night)

    def test_emergency_bypasses_opt_out_and_quiet_hours(self):
        with tenant_context(self.foundation.id):
            # User opted out of EMERGENCY
            NotificationPreference.objects.create(
                foundation_id=self.foundation.id,
                user=self.parent_user,
                category=NotificationCategory.EMERGENCY,
                enabled=False,
            )

            intent = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.parent_user,
                recipient_phone="+6281234567890",
                category=NotificationCategory.EMERGENCY,
                template_key='emergency.alert',
                payload={'message': 'Peringatan Banjir'},
                immediate=True,
            )
            # NTF-013: EMERGENCY cannot be opted out; must be DISPATCHED
            self.assertEqual(intent.status, IntentStatus.DISPATCHED)
            self.assertEqual(NotificationDelivery.objects.filter(intent=intent, status=DeliveryStatus.SENT).count(), 1)

    def test_daily_rate_limit_enforced_at_20_messages(self):
        with tenant_context(self.foundation.id):
            # Create 20 dispatched intents
            for i in range(20):
                NotificationIntent.objects.create(
                    foundation_id=self.foundation.id,
                    recipient_phone="+628999999999",
                    category=NotificationCategory.PAYMENT_RECEIVED,
                    template_key='finance.payment_received',
                    status=IntentStatus.DISPATCHED,
                )

            # 21st non-emergency intent should be cancelled due to rate limit (NTF-012)
            intent21 = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_phone="+628999999999",
                category=NotificationCategory.PAYMENT_RECEIVED,
                template_key='finance.payment_received',
                payload={'amount': '500000'},
            )
            self.assertEqual(intent21.status, IntentStatus.CANCELLED)
            self.assertIn('Batas harian', intent21.cancellation_reason)

    def test_fallback_ladder_on_whatsapp_failure(self):
        with tenant_context(self.foundation.id):
            # WhatsApp provider simulated failure
            self.mock_whatsapp.simulate_failure = True
            self.mock_whatsapp.simulate_failure_code = 'PROVIDER_DOWN'

            intent = dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.parent_user,
                recipient_phone="+6281234567890",
                category=NotificationCategory.ARRIVAL,
                template_key='attendance.arrival',
                payload={'student_name': 'Hasan', 'school_name': 'SMP Cendekia', 'gate_name': 'Pintu Timur', 'time': '07:05'},
                immediate=True,
            )

            # Assert intent dispatched via fallback channel (PUSH)
            self.assertEqual(intent.status, IntentStatus.DISPATCHED)
            deliveries = list(NotificationDelivery.objects.filter(intent=intent).order_by('created_at'))
            self.assertEqual(len(deliveries), 2)
            # First attempt: WhatsApp FAILED
            self.assertEqual(deliveries[0].channel, ChannelType.WHATSAPP)
            self.assertEqual(deliveries[0].status, DeliveryStatus.FAILED)
            # Second attempt: PUSH SENT
            self.assertEqual(deliveries[1].channel, ChannelType.PUSH)
            self.assertEqual(deliveries[1].status, DeliveryStatus.SENT)

            self.mock_whatsapp.simulate_failure = False

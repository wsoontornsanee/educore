from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.notifications.models import (
    ChannelType,
    DeliveryStatus,
    IntentStatus,
    NotificationCategory,
    NotificationDelivery,
    NotificationIntent,
    NotificationPreference,
    NotificationTemplate,
)
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class NotificationViewsAndCommandsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Darul Ilmi Nusantara",
            brand_name="Darul Ilmi",
            npwp="01.234.567.8-888.000",
            address="Surabaya",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Darul Ilmi",
            npsn="20400001",
            level=School.LEVEL_SMA,
        )
        self.admin_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628122334455",
            email="admin@darul.sch.id",
            full_name="Admin Darul",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628199887766",
            email="ahmad@example.sch.id",
            full_name="Ahmad Pratama",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.admin_user,
            role="school_admin",
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

    def test_my_notifications_and_mark_read(self):
        with tenant_context(self.foundation.id):
            intent = NotificationIntent.objects.create(
                foundation_id=self.foundation.id,
                recipient_user=self.parent_user,
                recipient_phone="+628199887766",
                category=NotificationCategory.ANNOUNCEMENT,
                template_key="school.announcement",
                payload={"title": "Libur Nasional"},
                status=IntentStatus.DISPATCHED,
            )
            delivery = NotificationDelivery.objects.create(
                foundation_id=self.foundation.id,
                intent=intent,
                channel=ChannelType.IN_APP,
                status=DeliveryStatus.SENT,
            )

        self.client.force_authenticate(user=self.parent_user)
        # List
        res = self.client.get('/api/v1/me/notifications/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

        # Mark read
        res_read = self.client.post(f'/api/v1/me/notifications/{delivery.id}/read/')
        self.assertEqual(res_read.status_code, 200)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, DeliveryStatus.READ)
        self.assertIsNotNone(delivery.read_at)

    def test_my_notification_preferences_get_and_put(self):
        self.client.force_authenticate(user=self.parent_user)

        # PUT preference
        put_data = {
            'category': 'PAYMENT_DUE',
            'channels': ['WHATSAPP', 'EMAIL'],
            'enabled': True,
            'quiet_hours_start': '22:00',
            'quiet_hours_end': '05:00',
        }
        res_put = self.client.put('/api/v1/me/notification-preferences/', put_data, format='json')
        self.assertEqual(res_put.status_code, 200)
        self.assertEqual(res_put.json()['channels'], ['WHATSAPP', 'EMAIL'])

        # GET preferences
        res_get = self.client.get('/api/v1/me/notification-preferences/')
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(len(res_get.json()), 1)
        self.assertEqual(res_get.json()[0]['category'], 'PAYMENT_DUE')

    def test_notification_deliveries_admin_view(self):
        with tenant_context(self.foundation.id):
            intent = NotificationIntent.objects.create(
                foundation_id=self.foundation.id,
                category=NotificationCategory.ARRIVAL,
                template_key="attendance.arrival",
                status=IntentStatus.DISPATCHED,
            )
            NotificationDelivery.objects.create(
                foundation_id=self.foundation.id,
                intent=intent,
                channel=ChannelType.WHATSAPP,
                status=DeliveryStatus.DELIVERED,
                provider_message_id="wamid.123",
            )

        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(f'/api/v1/notifications/deliveries/?category=ARRIVAL&status=DELIVERED&school_id={self.school.id}')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data.get('results', data)
        self.assertEqual(len(results), 1)

    def test_send_due_notifications_command_execution(self):
        with tenant_context(self.foundation.id):
            # Create a pending intent scheduled in past
            NotificationIntent.objects.create(
                foundation_id=self.foundation.id,
                recipient_phone="+628199887766",
                category=NotificationCategory.PAYMENT_RECEIVED,
                template_key="finance.payment_received",
                payload={"amount": "1500000", "invoice_number": "INV-001"},
                scheduled_for=timezone.now() - timezone.timedelta(minutes=5),
                status=IntentStatus.PENDING,
            )

        out = StringIO()
        call_command('send_due_notifications', limit=10, stdout=out)
        output = out.getvalue()
        self.assertIn('Successfully processed', output)

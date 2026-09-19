"""Decal anomaly alerts (spec 18 QRS-041): volume and out-of-hours."""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import RoleAssignment, User
from apps.notifications.models import NotificationCategory, NotificationIntent
from apps.wallet.models import Merchant, POSTransaction
from apps.wallet.qr_alerts import check_static_charge_anomalies
from apps.wallet.qr_charge import charge_qr_session
from apps.wallet.qr_decals import create_payment_point, decal_token, print_decal
from apps.wallet.services import topup_wallet
from apps.wallet.tests.test_qr_charge import make_guardian
from apps.wallet.tests.test_qr_static import StaticFixture
from educore.middleware.tenancy import set_current_foundation_id


def alerts():
    return NotificationIntent.all_tenants.filter(category=NotificationCategory.QR_DECAL_ALERT)


class AlertBase(StaticFixture):
    def setUp(self):
        super().setUp()
        self.decal_obj = self.decal()
        self.admins = []
        for i in range(2):
            u = User.objects.create(
                foundation_id=self.fx['foundation'].id, phone_e164=f"+628140000000{i}", email=f"a{i}@s.id", full_name=f"Admin {i}",
            )
            RoleAssignment.all_tenants.create(
                foundation_id=self.fx['foundation'].id, user=u, role='school_admin',
                scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
            )
            self.admins.append(u)
        topup_wallet(self.wallet, Decimal('5000000'), 'CASH', 'big')

    def charge(self, key, at=None):
        set_current_foundation_id(self.fx['foundation'].id)
        if at is None:
            return charge_qr_session(decal_token(self.decal_obj), self.student, Decimal('1000'), key)
        with mock.patch('apps.wallet.qr_charge.timezone.now', return_value=at):
            return charge_qr_session(decal_token(self.decal_obj), self.student, Decimal('1000'), key)

    def set_hours(self, start, end):
        self.merchant.operating_start, self.merchant.operating_end = start, end
        self.merchant.save()


class VolumeAlertTests(AlertBase, TestCase):
    def test_alert_fires_once_when_threshold_crossed_to_every_admin(self):
        self.merchant.static_decal_daily_alert = 3
        self.merchant.save()
        for i in range(3):
            self.charge(f'k{i}')
        self.assertEqual(alerts().count(), 0)
        self.charge('k3')
        self.assertEqual(alerts().count(), 2)  # both admins
        self.assertEqual({a.recipient_user_id for a in alerts()}, {u.id for u in self.admins})
        intent = alerts().first()
        self.assertEqual(intent.template_key, 'wallet.decal_alert')
        self.assertEqual(intent.payload['human_id'], self.decal_obj.human_id)
        self.assertIn('4 pembayaran', intent.payload['reason_text'])
        for i in range(4, 8):
            self.charge(f'k{i}')
        self.assertEqual(alerts().count(), 2)  # dampened: no repeat the same day
        self.assertEqual(AuditEvent.objects.filter(action='wallet.decal.alert').count(), 1)

    def test_no_alert_at_or_below_threshold(self):
        self.merchant.static_decal_daily_alert = 5
        self.merchant.save()
        for i in range(5):
            self.charge(f'k{i}')
        self.assertEqual(alerts().count(), 0)

    def test_terminal_sessions_never_alert(self):
        from apps.wallet.qr_charge import create_qr_session
        self.merchant.static_decal_daily_alert = 1
        self.merchant.save()
        set_current_foundation_id(self.fx['foundation'].id)
        for i in range(3):
            charge_qr_session(create_qr_session(self.terminal)['token'], self.student, Decimal('1000'), f't{i}')
        self.assertEqual(alerts().count(), 0)

    def test_counts_are_per_sheet(self):
        other = print_decal(create_payment_point(self.merchant, 'Fotokopi', '', self.operator), self.operator)
        self.merchant.static_decal_daily_alert = 2
        self.merchant.save()
        set_current_foundation_id(self.fx['foundation'].id)
        for i in range(2):
            self.charge(f'a{i}')
            charge_qr_session(decal_token(other), self.student, Decimal('1000'), f'b{i}')
        self.assertEqual(alerts().count(), 0)


class OutOfHoursAlertTests(AlertBase, TestCase):
    def local_at(self, hour, minute=0):
        from apps.attendance.services import get_school_timezone
        tz = get_school_timezone(self.fx['school'])
        return datetime.datetime.combine(timezone.now().astimezone(tz).date(), datetime.time(hour, minute), tzinfo=tz)

    def test_charge_outside_hours_alerts_inside_does_not(self):
        self.set_hours(datetime.time(7, 0), datetime.time(15, 0))
        self.charge('in', at=self.local_at(10))
        self.assertEqual(alerts().count(), 0)
        self.charge('out', at=self.local_at(3, 0))
        self.assertEqual(alerts().count(), 2)
        self.assertIn('03:00', alerts().first().payload['reason_text'])
        self.assertEqual(alerts().first().payload['reason'], 'OUT_OF_HOURS')

    def test_boundaries_are_inside(self):
        self.set_hours(datetime.time(7, 0), datetime.time(15, 0))
        self.charge('a', at=self.local_at(7, 0))
        self.charge('b', at=self.local_at(15, 0))
        self.assertEqual(alerts().count(), 0)

    def test_window_crossing_midnight(self):
        self.set_hours(datetime.time(20, 0), datetime.time(2, 0))
        self.charge('a', at=self.local_at(23, 30))
        self.charge('b', at=self.local_at(1, 0))
        self.assertEqual(alerts().count(), 0)
        self.charge('c', at=self.local_at(12, 0))
        self.assertEqual(alerts().count(), 2)

    def test_no_hours_configured_means_no_out_of_hours_alert(self):
        self.charge('x', at=self.local_at(3, 0))
        self.assertEqual(alerts().count(), 0)

    def test_repeated_out_of_hours_charges_alert_once_per_day(self):
        self.set_hours(datetime.time(7, 0), datetime.time(15, 0))
        for i in range(4):
            self.charge(f'k{i}', at=self.local_at(3, i))
        self.assertEqual(alerts().count(), 2)


class AlertRobustnessTests(AlertBase, TestCase):
    def test_alert_failure_never_breaks_the_payment(self):
        self.set_hours(datetime.time(7, 0), datetime.time(7, 1))
        with mock.patch('apps.wallet.qr_alerts.dispatch_intent', side_effect=RuntimeError('boom')):
            pos_tx = self.charge('x')
        self.assertEqual(pos_tx.status, 'COMPLETED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('5000000') + Decimal('100000') - Decimal('1000'))

    def test_no_admins_still_pays_and_audits_zero_recipients(self):
        RoleAssignment.all_tenants.filter(role='school_admin').delete()
        self.merchant.static_decal_daily_alert = 1
        self.merchant.save()
        self.charge('a')
        self.charge('b')
        self.assertEqual(alerts().count(), 0)

    def test_idempotent_retry_does_not_realert(self):
        self.merchant.static_decal_daily_alert = 1
        self.merchant.save()
        self.charge('a')
        self.charge('b')
        n = alerts().count()
        with mock.patch('apps.wallet.qr_alerts.check_static_charge_anomalies') as spy:
            self.charge('b')
        spy.assert_not_called()
        self.assertEqual(alerts().count(), n)

    def test_direct_check_ignores_rejected_and_terminal_rows(self):
        pos_tx = self.charge('a')
        POSTransaction.objects.filter(id=pos_tx.id).update(status='VOIDED')
        pos_tx.refresh_from_db()
        self.assertEqual(check_static_charge_anomalies(pos_tx), [])


class AlertSettingsTests(AlertBase, TestCase):
    def test_serializer_needs_both_hours_and_positive_threshold(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admins[0])
        url = f'/api/v1/merchants/{self.merchant.id}/'
        res = client.patch(url, {'operating_start': '07:00:00'}, format='json')
        self.assertEqual(res.status_code, 400)
        res = client.patch(url, {'operating_start': '07:00:00', 'operating_end': '15:00:00', 'static_decal_daily_alert': 200}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.merchant.refresh_from_db()
        self.assertEqual(self.merchant.static_decal_daily_alert, 200)


class AlertSettingsPageTests(TestCase):
    """Admin page form for hours + threshold (school_config.write only)."""

    def setUp(self):
        from apps.wallet.tests.test_qr_console_web import QRConsoleBase
        self.base = QRConsoleBase
        QRConsoleBase.setUp(self)
        self.enable()
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        self.url = f'/web/wallet/canteen/qr/merchants/{self.merchant.id}/alerts/'

    enable = lambda self: self.base.enable(self)
    as_user = lambda self, u: self.base.as_user(self, u)

    def reload(self):
        return Merchant.all_tenants.get(id=self.merchant.id)

    def test_admin_saves_hours_and_threshold(self):
        self.as_user(self.admin)
        self.assertContains(self.client.get('/web/wallet/canteen/qr/'), 'Peringatan lembar QR')
        self.client.post(self.url, {'operating_start': '07:00', 'operating_end': '15:30', 'static_decal_daily_alert': '150'})
        m = self.reload()
        self.assertEqual((m.operating_start, m.operating_end, m.static_decal_daily_alert),
                         (datetime.time(7, 0), datetime.time(15, 30), 150))

    def test_half_window_and_bad_threshold_are_refused(self):
        self.as_user(self.admin)
        for data in (
            {'operating_start': '07:00', 'operating_end': '', 'static_decal_daily_alert': '150'},
            {'operating_start': '07:00', 'operating_end': '99:99', 'static_decal_daily_alert': '150'},
            {'operating_start': '', 'operating_end': '', 'static_decal_daily_alert': '0'},
            {'operating_start': '', 'operating_end': '', 'static_decal_daily_alert': 'abc'},
        ):
            self.client.post(self.url, data)
        m = self.reload()
        self.assertEqual((m.operating_start, m.static_decal_daily_alert), (None, 300))

    def test_blank_hours_turn_out_of_hours_off(self):
        self.as_user(self.admin)
        self.client.post(self.url, {'operating_start': '07:00', 'operating_end': '15:00', 'static_decal_daily_alert': '300'})
        self.client.post(self.url, {'operating_start': '', 'operating_end': '', 'static_decal_daily_alert': '300'})
        self.assertIsNone(self.reload().operating_start)

    def test_finance_cannot_change_settings(self):
        self.as_user(self.finance)
        self.client.post(self.url, {'operating_start': '07:00', 'operating_end': '15:00', 'static_decal_daily_alert': '5'})
        self.assertEqual(self.reload().static_decal_daily_alert, 300)

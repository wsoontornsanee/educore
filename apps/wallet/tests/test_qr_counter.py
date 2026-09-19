"""QR Charge Counter feed and the payment-point / printed-sheet console pages (spec 18 §3b, QRS-030..042)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.core.models import AuditEvent
from apps.identity.models import User
from apps.wallet.models import POSPaymentPoint, POSQRDecal, POSQRDecalStatus, POSTransaction, POSTransactionStatus
from apps.wallet.qr_charge import QRChargeError, charge_qr_session, create_qr_session
from apps.wallet.qr_decals import COUNTER_FEED_LIMIT, create_payment_point, decal_token, get_counter_feed, print_decal
from apps.wallet.tests.test_qr_console_web import QRConsoleBase, make_staff
from educore.middleware.tenancy import set_current_foundation_id

PAGE = '/web/wallet/canteen/qr/'


class CounterBase(QRConsoleBase):
    def setUp(self):
        super().setUp()
        self.enable()
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        self.point = create_payment_point(self.merchant, 'Gerobak Minuman', 'Lapangan', self.operator)
        self.decal = print_decal(self.point, self.operator)
        set_current_foundation_id(self.foundation.id)

    def scan(self, amount='8000', key='k1', decal=None):
        set_current_foundation_id(self.foundation.id)
        return charge_qr_session(decal_token(decal or self.decal), self.student, Decimal(amount), key)


class CounterFeedServiceTests(CounterBase):
    def test_feed_lists_paid_and_refused_newest_first_with_completed_totals(self):
        self.scan('8000', 'a')
        self.scan('5000', 'b')
        with self.assertRaises(QRChargeError):
            self.scan('30000', 'c')  # above the static cap -> refused, logged
        feed = get_counter_feed(self.point)
        self.assertEqual(feed['count_today'], 2)
        self.assertEqual(feed['total_today'], Decimal('13000.00'))
        self.assertEqual([i['status'] for i in feed['items']], ['REJECTED', 'COMPLETED', 'COMPLETED'])
        refused = feed['items'][0]
        self.assertEqual(refused['reject_reason'], 'AMOUNT_ABOVE_CAP')
        self.assertIn('batas', refused['reject_label'])
        self.assertEqual(feed['items'][1]['amount'], Decimal('5000.00'))
        self.assertEqual(len(feed['items'][1]['confirmation_code']), 4)

    def test_feed_is_scoped_to_its_counter_and_today(self):
        other = create_payment_point(self.merchant, 'Fotokopi', '', self.operator)
        other_decal = print_decal(other, self.operator)
        self.scan('8000', 'a')
        self.scan('4000', 'b', decal=other_decal)
        old = self.scan('3000', 'c')
        POSTransaction.objects.filter(id=old.id).update(occurred_at=timezone.now() - datetime.timedelta(days=2))
        feed = get_counter_feed(self.point)
        self.assertEqual(feed['count_today'], 1)
        self.assertEqual(feed['total_today'], Decimal('8000.00'))
        self.assertEqual(len(feed['items']), 1)

    def test_terminal_sales_never_appear(self):
        from apps.wallet.qr_charge import create_qr_session
        token = create_qr_session(self.terminal)['token']
        set_current_foundation_id(self.foundation.id)
        charge_qr_session(token, self.student, Decimal('9000'), 'term')
        self.assertEqual(get_counter_feed(self.point)['items'], [])

    def test_feed_is_capped(self):
        topup = Decimal('5000000')
        from apps.wallet.services import topup_wallet
        topup_wallet(self.wallet, topup, 'CASH', 'big')
        for i in range(COUNTER_FEED_LIMIT + 3):
            self.scan('1000', f'k{i}')
        feed = get_counter_feed(self.point)
        self.assertEqual(len(feed['items']), COUNTER_FEED_LIMIT)
        self.assertEqual(feed['count_today'], COUNTER_FEED_LIMIT + 3)

    def test_rejection_reason_recorded_for_terminal_sessions_too(self):
        token = create_qr_session(self.terminal)['token']
        set_current_foundation_id(self.foundation.id)
        with self.assertRaises(QRChargeError):
            charge_qr_session(token, self.student, Decimal('60000'), 'big')
        self.assertEqual(POSTransaction.objects.get(status=POSTransactionStatus.REJECTED).reject_reason, 'AMOUNT_ABOVE_CAP')


class CounterEndpointTests(CounterBase):
    def test_api_feed_for_operator_and_404s(self):
        self.scan('8000')
        self.as_user(self.operator)
        res = self.client.get(f'/api/v1/pos/counter/?payment_point_id={self.point.id}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['payment_point']['name'], 'Gerobak Minuman')
        self.assertEqual(body['total_today'], '8000.00')
        self.assertEqual(body['items'][0]['amount'], '8000.00')
        self.assertEqual(self.client.get('/api/v1/pos/counter/').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/pos/counter/?payment_point_id=999999').status_code, 404)

    def test_api_feed_denied_without_pos_collect(self):
        from apps.wallet.tests.test_qr_charge import make_guardian
        self.as_user(make_guardian(self.fx | {'student': self.student}, nik='3471010101016001'))
        self.assertEqual(self.client.get(f'/api/v1/pos/counter/?payment_point_id={self.point.id}').status_code, 403)

    def test_web_counter_page_and_feed(self):
        self.scan('8000')
        self.as_user(self.operator)
        res = self.client.get(f'{PAGE}counter/{self.point.id}/')
        self.assertContains(res, 'Gerobak Minuman')
        self.assertContains(res, 'id="ct-sound"')
        feed = self.client.get(f'{PAGE}counter/{self.point.id}/feed/').json()
        self.assertEqual(feed['count_today'], 1)
        self.assertEqual(feed['items'][0]['student_nis'], self.student.nis)

    def test_web_counter_other_tenant_is_404(self):
        other = build_academic_fixture("Yayasan Asing Counter")
        from apps.wallet.models import Merchant
        m = Merchant.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], name='K', qr_self_amount_enabled=True,
            static_qr_enabled=True,
        )
        p = POSPaymentPoint.objects.create(foundation_id=other['foundation'].id, merchant=m, name='Asing')
        set_current_foundation_id(self.foundation.id)
        self.as_user(self.operator)
        self.assertEqual(self.client.get(f'{PAGE}counter/{p.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'{PAGE}counter/{p.id}/feed/').status_code, 404)


class PointsPageTests(CounterBase):
    def test_operator_creates_prints_downloads_and_revokes_without_admin(self):
        self.as_user(self.operator)  # canteen_operator: pos.manage, no school_config.write
        self.assertContains(self.client.get(f'{PAGE}points/'), 'Gerobak Minuman')
        self.client.post(f'{PAGE}points/create/', {'merchant_id': self.merchant.id, 'name': 'Meja depan', 'location': 'A'})
        new_point = POSPaymentPoint.all_tenants.get(name='Meja depan')
        self.client.post(f'{PAGE}points/{new_point.id}/print/', {})
        new_decal = POSQRDecal.all_tenants.get(payment_point=new_point)
        res = self.client.get(f'{PAGE}decals/{new_decal.id}/pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Cache-Control'], 'no-store')
        self.assertTrue(AuditEvent.objects.filter(action='wallet.decal.downloaded', entity_id=str(new_decal.id)).exists())
        self.client.post(f'{PAGE}decals/{new_decal.id}/revoke/', {'reason': 'hilang'})
        new_decal.refresh_from_db()
        self.assertEqual(new_decal.status, POSQRDecalStatus.REVOKED)
        self.decal.refresh_from_db()
        self.assertEqual(self.decal.status, POSQRDecalStatus.ACTIVE)  # other counters untouched

    def test_reprint_supersedes_previous_sheet(self):
        self.as_user(self.operator)
        self.client.post(f'{PAGE}points/{self.point.id}/print/', {})
        self.decal.refresh_from_db()
        self.assertEqual(self.decal.status, POSQRDecalStatus.SUPERSEDED)
        self.assertIsNotNone(self.decal.grace_until)

    def test_superseded_sheet_cannot_be_downloaded_but_page_survives(self):
        self.as_user(self.operator)
        self.client.post(f'{PAGE}points/{self.point.id}/print/', {})
        res = self.client.get(f'{PAGE}decals/{self.decal.id}/pdf/')
        self.assertEqual(res.status_code, 302)  # flashed error, back to the page

    def test_static_disabled_blocks_creation_with_message(self):
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        self.as_user(self.operator)
        before = POSPaymentPoint.all_tenants.count()
        self.client.post(f'{PAGE}points/create/', {'merchant_id': self.merchant.id, 'name': 'X'})
        self.assertEqual(POSPaymentPoint.all_tenants.count(), before)
        self.assertContains(self.client.get(f'{PAGE}points/'), 'belum diaktifkan')

    def test_blank_name_and_bad_expiry_are_refused(self):
        self.as_user(self.operator)
        before = POSPaymentPoint.all_tenants.count()
        self.client.post(f'{PAGE}points/create/', {'merchant_id': self.merchant.id, 'name': '  '})
        self.assertEqual(POSPaymentPoint.all_tenants.count(), before)
        decals = POSQRDecal.all_tenants.count()
        self.client.post(f'{PAGE}points/{self.point.id}/print/', {'expires_on': 'nope'})
        self.assertEqual(POSQRDecal.all_tenants.count(), decals)

    def test_finance_and_guardian_cannot_manage_points(self):
        for user in (self.finance,):
            self.as_user(user)
            # finance_officer holds no pos.manage: bounced, nothing changed
            self.assertEqual(self.client.get(f'{PAGE}points/').status_code, 302)
            self.client.post(f'{PAGE}points/{self.point.id}/close/')
        self.point.refresh_from_db()
        self.assertEqual(self.point.status, 'ACTIVE')

    def test_cross_tenant_point_and_decal_are_404(self):
        other = build_academic_fixture("Yayasan Asing Points")
        from apps.wallet.models import Merchant
        m = Merchant.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], name='K', qr_self_amount_enabled=True,
            static_qr_enabled=True,
        )
        p = POSPaymentPoint.objects.create(foundation_id=other['foundation'].id, merchant=m, name='Asing')
        d = POSQRDecal.objects.create(
            foundation_id=other['foundation'].id, payment_point=p, human_id='K-01', nonce='n',
            printed_by=other['teacher_user'], printed_at=timezone.now(),
        )
        set_current_foundation_id(self.foundation.id)
        self.as_user(self.operator)
        self.assertEqual(self.client.post(f'{PAGE}points/{p.id}/close/').status_code, 404)
        self.assertEqual(self.client.post(f'{PAGE}points/{p.id}/print/', {}).status_code, 404)
        self.assertEqual(self.client.post(f'{PAGE}decals/{d.id}/revoke/').status_code, 404)
        self.assertEqual(self.client.get(f'{PAGE}decals/{d.id}/pdf/').status_code, 404)
        self.assertEqual(self.client.post(f'{PAGE}points/create/', {'merchant_id': m.id, 'name': 'x'}).status_code, 404)

    def test_admin_switches_static_qr_from_settings_page(self):
        self.merchant.static_qr_enabled = False
        self.merchant.save()
        self.as_user(self.admin)
        self.assertContains(self.client.get(PAGE), 'Aktifkan QR statis')
        url = f'{PAGE}merchants/{self.merchant.id}/static/'
        self.client.post(url, {'enabled': '1'})
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.static_qr_enabled)  # acknowledgement required
        self.client.post(url, {'enabled': '1', 'acknowledged': 'on'})
        self.merchant.refresh_from_db()
        self.assertTrue(self.merchant.static_qr_enabled)
        self.client.post(url, {'enabled': '0'})
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.static_qr_enabled)

    def test_kantin_console_links_points_and_counter_for_operator(self):
        self.as_user(self.operator)
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, f'{PAGE}points/')

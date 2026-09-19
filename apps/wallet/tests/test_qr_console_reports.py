"""QR Charge console reporting: entry mode in sales lists (QRS-003), sales by counter (QRS-040), dispute history."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.wallet.models import POSTransaction
from apps.wallet.qr_charge import charge_qr_session, create_qr_session
from apps.wallet.qr_decals import (
    create_payment_point,
    decal_token,
    get_sales_by_payment_point,
    local_day_bounds,
    print_decal,
)
from apps.wallet.qr_oversight import open_qr_dispute, resolve_qr_dispute
from apps.wallet.services import process_pos_transaction
from apps.wallet.tests.test_qr_charge import make_guardian
from apps.wallet.tests.test_qr_console_web import PAGE, QRConsoleBase
from educore.middleware.tenancy import set_current_foundation_id


class ReportBase(QRConsoleBase):
    def setUp(self):
        super().setUp()
        self.enable()
        self.merchant.static_qr_enabled = True
        self.merchant.save()
        self.point = create_payment_point(self.merchant, 'Gerobak Minuman', 'Lapangan', self.operator)
        self.decal = print_decal(self.point, self.operator)
        set_current_foundation_id(self.foundation.id)

    def static_charge(self, amount, key):
        set_current_foundation_id(self.foundation.id)
        return charge_qr_session(decal_token(self.decal), self.student, Decimal(amount), key)

    def terminal_charge(self, amount, key):
        set_current_foundation_id(self.foundation.id)
        return charge_qr_session(create_qr_session(self.terminal)['token'], self.student, Decimal(amount), key)

    def card_sale(self, cid, unit_price='6000.00'):
        set_current_foundation_id(self.foundation.id)
        return process_pos_transaction(
            self.terminal, self.student, [{'sku': 'X', 'name': 'Roti', 'qty': 1, 'unit_price': unit_price}], cid,
        )


class SalesByPointServiceTests(ReportBase):
    def test_groups_by_counter_with_non_decal_row_last(self):
        other = print_decal(create_payment_point(self.merchant, 'Fotokopi', '', self.operator), self.operator)
        self.static_charge('5000', 'a')
        self.static_charge('3000', 'b')
        set_current_foundation_id(self.foundation.id)
        charge_qr_session(decal_token(other), self.student, Decimal('4000'), 'c')
        self.terminal_charge('9000', 't')
        self.card_sale('card-1')
        rows = get_sales_by_payment_point(POSTransaction.objects.filter(foundation_id=self.foundation.id, status='COMPLETED'))
        by = {r['payment_point_name']: r for r in rows}
        self.assertEqual((by['Gerobak Minuman']['count'], by['Gerobak Minuman']['total']), (2, Decimal('8000.00')))
        self.assertEqual((by['Fotokopi']['count'], by['Fotokopi']['total']), (1, Decimal('4000.00')))
        self.assertEqual((by[None]['count'], by[None]['total']), (2, Decimal('15000.00')))  # terminal QR + card-tap
        self.assertIsNone(rows[-1]['payment_point_name'])

    def test_local_day_bounds(self):
        day, start, end = local_day_bounds(self.school)
        self.assertEqual(end - start, datetime.timedelta(days=1))
        self.assertEqual(start.date(), day)


class ConsolePagesTests(ReportBase):
    def test_kantin_console_marks_self_entered_and_names_the_counter(self):
        self.static_charge('8000', 'a')
        self.card_sale('card-1')
        self.as_user(self.operator)
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, 'Diinput siswa (QR)')
        self.assertContains(res, 'Gerobak Minuman')
        self.assertContains(res, 'Petugas')
        code = POSTransaction.all_tenants.get(qr_decal=self.decal).confirmation_code
        self.assertContains(res, code)

    def test_admin_page_shows_sales_by_counter_for_the_day(self):
        self.static_charge('8000', 'a')
        self.card_sale('card-1')
        self.as_user(self.admin)
        res = self.client.get(PAGE)
        self.assertContains(res, 'Penjualan per titik pembayaran')
        self.assertContains(res, 'Gerobak Minuman')
        self.assertContains(res, 'Kasir / terminal (bukan lembar QR)')
        other_day = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()
        self.assertNotContains(self.client.get(PAGE, {'date': other_day}), 'Kasir / terminal (bukan lembar QR)')

    def test_admin_page_lists_resolved_disputes_only_recent(self):
        guardian = make_guardian(self.fx | {'student': self.student}, nik='3471010101017001')
        first = self.static_charge('8000', 'a')
        second = self.static_charge('5000', 'b')
        d1 = resolve_qr_dispute(open_qr_dispute(first, guardian, 'x'), 'UPHELD', self.admin, 'benar salah')
        d2 = resolve_qr_dispute(open_qr_dispute(second, guardian, 'y'), 'REJECTED', self.admin, 'sudah benar')
        old = self.static_charge('2000', 'c')
        d3 = resolve_qr_dispute(open_qr_dispute(old, guardian, 'z'), 'REJECTED', self.admin, 'terlalu lama')
        from apps.wallet.models import QRDispute
        QRDispute.all_tenants.filter(id=d3.id).update(resolved_at=timezone.now() - datetime.timedelta(days=40))
        self.as_user(self.admin)
        res = self.client.get(PAGE)
        self.assertContains(res, 'Sanggahan selesai (30 hari)')
        self.assertContains(res, 'benar salah')
        self.assertContains(res, 'sudah benar')
        self.assertNotContains(res, 'terlalu lama')

    def test_finance_sees_reports_but_no_actions(self):
        self.static_charge('8000', 'a')
        self.as_user(self.finance)
        res = self.client.get(PAGE)
        self.assertContains(res, 'Penjualan per titik pembayaran')
        self.assertNotContains(res, 'Aktifkan QR statis')

    def test_sales_api_helper_unchanged(self):
        self.static_charge('8000', 'a')
        self.as_user(self.finance)
        rows = self.client.get(f'/api/v1/merchants/{self.merchant.id}/sales/?group_by=payment_point').json()
        self.assertEqual(rows[0]['payment_point_name'], 'Gerobak Minuman')
        self.assertEqual(rows[0]['total'], '8000.00')

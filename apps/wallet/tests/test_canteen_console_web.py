"""Kantin & dompet web console (GET /web/wallet/canteen/) and its snapshot service."""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import RoleAssignment
from apps.wallet.models import Merchant, MerchantType, POSTransaction, POSTerminal, POSTransactionStatus, WalletStatus
from apps.wallet.services import get_canteen_console_snapshot, get_or_create_wallet, topup_wallet
from educore.middleware.tenancy import set_current_foundation_id


class CanteenConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Kantin Web")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.fx['teacher_user'], role='canteen_operator',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.merchant = Merchant.objects.create(
            foundation_id=self.foundation.id, school=self.school, name="Kantin Sehat", type=MerchantType.CANTEEN,
        )
        self.terminal = POSTerminal.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, device_id="TERM-KW-1", name="Kasir 1",
        )
        self.wallet = get_or_create_wallet(self.student)
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-kw')
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def _sale(self, cid, total, status=POSTransactionStatus.COMPLETED, offline=False, when=None):
        return POSTransaction.objects.create(
            foundation_id=self.foundation.id, merchant=self.merchant, terminal=self.terminal, student=self.student,
            items=[], subtotal=Decimal(total), total=Decimal(total), occurred_at=when or timezone.now(),
            status=status, offline_created=offline, client_transaction_id=cid,
        )

    def test_snapshot_totals_only_todays_completed_sales(self):
        self._sale('a', '15000.00')
        self._sale('b', '10000.50', offline=True)
        self._sale('c', '99999.00', status=POSTransactionStatus.REJECTED)
        self._sale('d', '88888.00', when=timezone.now() - timezone.timedelta(days=2))
        snap = get_canteen_console_snapshot(self.foundation.id, self.school)
        self.assertEqual(snap['sales_total'], Decimal('25000.50'))
        self.assertEqual(snap['sales_count'], 2)
        self.assertEqual(snap['offline_count'], 1)
        row = snap['merchants'][0]
        self.assertEqual((row['sales_total'], row['sales_count'], row['active_terminals']), (Decimal('25000.50'), 2, 1))
        self.assertEqual(len(snap['recent_transactions']), 4)

    def test_snapshot_wallet_counts_and_balance(self):
        snap = get_canteen_console_snapshot(self.foundation.id, self.school)
        self.assertEqual((snap['wallets_active'], snap['wallets_frozen']), (1, 0))
        self.assertEqual(snap['wallets_balance'], Decimal('100000.00'))
        self.wallet.status = WalletStatus.FROZEN
        self.wallet.save(update_fields=['status'])
        snap = get_canteen_console_snapshot(self.foundation.id, self.school)
        self.assertEqual((snap['wallets_active'], snap['wallets_frozen']), (0, 1))
        self.assertEqual(snap['wallets_balance'], Decimal('0.00'))

    def test_page_renders_money_in_idr_format(self):
        self._sale('a', '15000.00')
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, 'Kantin &amp; dompet')
        self.assertContains(res, 'Kantin Sehat')
        self.assertContains(res, 'Rp 15.000')
        self.assertContains(res, 'Rp 100.000')  # total wallet balance

    def test_page_empty_state_without_merchants(self):
        Merchant.objects.filter(pk=self.merchant.pk).update(deleted_at=timezone.now())
        res = self.client.get('/web/wallet/canteen/')
        self.assertContains(res, 'Belum ada merchant terdaftar')
        self.assertContains(res, 'Belum ada transaksi POS.')

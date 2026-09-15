import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.wallet.models import WalletReconciliation, WalletReconciliationStatus
from apps.wallet.services import (
    get_reconciliation_queue,
    get_school_reconciliation_exposure,
    resend_reconciliation_notice,
    settle_reconciliation_with_cash,
    write_off_reconciliation_case,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_offline_sync import batch_item
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case


class ReconciliationQueueTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_queue_lists_open_case_with_exposure_header(self):
        rows = get_reconciliation_queue(self.fx['school'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], WalletReconciliationStatus.OPEN)
        self.assertEqual(rows[0]['shortfall'], str(self.case.shortfall))

        exposure = get_school_reconciliation_exposure(self.fx['school'])
        self.assertEqual(exposure, self.case.shortfall)

    def test_settled_case_excluded_from_open_queue(self):
        settle_reconciliation_with_cash(self.case, self.case.shortfall, "Tunai")
        rows = get_reconciliation_queue(self.fx['school'])
        self.assertEqual(rows, [])
        self.assertEqual(get_school_reconciliation_exposure(self.fx['school']), Decimal('0.00'))


class CashSettlementTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_cash_settlement_marks_case_settled(self):
        settle_reconciliation_with_cash(self.case, self.case.shortfall, "Tunai di kantor")
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.SETTLED)
        self.fx['student'].wallet.refresh_from_db()
        self.assertFalse(self.fx['student'].wallet.requires_reconciliation)


class WriteOffTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_write_off_marks_case_and_restores_balance(self):
        wallet = self.case.wallet
        balance_before = wallet.balance

        write_off_reconciliation_case(self.case, None, "Kesalahan sistem")

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.WRITTEN_OFF)
        self.assertEqual(self.case.write_off_reason, "Kesalahan sistem")

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, balance_before + self.case.shortfall)

    def test_write_off_does_not_settle_other_open_cases(self):
        # A second, real overspend for the same wallet/day so the balance actually
        # reflects two independent debts (not a fabricated row detached from reality).
        from apps.wallet.services import process_offline_pos_batch
        process_offline_pos_batch(
            self.terminal, [batch_item(self.product.sku, self.product.name, 'off-second', student_id=self.fx['student'].id)]
        )
        other_case = WalletReconciliation.all_tenants.filter(wallet=self.case.wallet).exclude(id=self.case.id).get()

        write_off_reconciliation_case(self.case, None, "Kesalahan sistem")

        other_case.refresh_from_db()
        self.assertEqual(other_case.status, WalletReconciliationStatus.OPEN)
        self.case.wallet.refresh_from_db()
        self.assertLess(self.case.wallet.balance, Decimal('0.00'))


class ResendNoticeTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_resend_creates_new_intent(self):
        from apps.notifications.models import NotificationIntent
        before = NotificationIntent.all_tenants.filter(foundation_id=self.fx['foundation'].id).count()
        resend_reconciliation_notice(self.case)
        after = NotificationIntent.all_tenants.filter(foundation_id=self.fx['foundation'].id).count()
        self.assertGreater(after, before)


class ReconciliationAdminViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def test_queue_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/wallet-reconciliations/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['cases']), 1)
        self.assertEqual(res.json()['total_exposure'], str(self.case.shortfall))

    def test_settle_cash_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/settle-cash/', {
            'amount': str(self.case.shortfall), 'reference': 'Tunai',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], WalletReconciliationStatus.SETTLED)

    def test_write_off_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/write-off/', {
            'reason': 'Terminal error',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], WalletReconciliationStatus.WRITTEN_OFF)

    def test_invoice_now_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/invoice-now/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], WalletReconciliationStatus.INVOICED)
        self.assertIsNotNone(res.json()['invoice_id'])

    def test_cross_tenant_case_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Admin Queue B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/write-off/', {
            'reason': 'x',
        }, format='json')
        self.assertEqual(res.status_code, 404)

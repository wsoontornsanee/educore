import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import JobRun
from apps.reporting.models import RptWalletActivity
from apps.reporting.services import refresh_wallet_activity
from apps.wallet.models import WalletTransactionType
from apps.wallet.services import (
    get_or_create_wallet,
    process_pos_transaction,
    record_wallet_transaction,
    topup_wallet,
)
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class RefreshWalletActivityTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_computes_topups_purchases_commission_active_wallets(self):
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')
        process_pos_transaction(self.terminal, self.fx['student'], [
            {'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': str(self.product.price)},
        ], client_transaction_id='pos-1')

        refresh_wallet_activity(scope='full')

        today = timezone.now().date()
        row = RptWalletActivity.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=today)
        self.assertEqual(row.topups, Decimal('100000.00'))
        self.assertEqual(row.purchases, Decimal('15000.00'))
        self.assertEqual(row.commission, Decimal('300.00'))  # 2% of 15000
        self.assertEqual(row.active_wallets, 1)
        self.assertIsNotNone(row.computed_at)

    def test_dashboard_scope_excludes_old_days_full_scope_includes_them(self):
        old_date = timezone.now() - datetime.timedelta(days=10)
        record_wallet_transaction(
            self.wallet, WalletTransactionType.TOPUP, Decimal('50000'), 'old-seed', occurred_at=old_date,
        )

        refresh_wallet_activity(scope='dashboard')
        self.assertFalse(RptWalletActivity.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, date=old_date.date(),
        ).exists())

        refresh_wallet_activity(scope='full')
        row = RptWalletActivity.all_tenants.get(foundation_id=self.fx['foundation'].id, date=old_date.date())
        self.assertEqual(row.topups, Decimal('50000.00'))

    def test_rerun_is_idempotent(self):
        topup_wallet(self.wallet, Decimal('20000'), 'CASH', 'seed-2')
        refresh_wallet_activity(scope='full')
        refresh_wallet_activity(scope='full')

        today = timezone.now().date()
        self.assertEqual(
            RptWalletActivity.all_tenants.filter(foundation_id=self.fx['foundation'].id, school=self.fx['school'], date=today).count(),
            1,
        )

    def test_management_command_creates_job_run(self):
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'seed-3')
        call_command('refresh_reporting', '--scope=full')

        job_run = JobRun.objects.filter(job_name='refresh_reporting_full').order_by('-id').first()
        self.assertIsNotNone(job_run)
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)


class WalletActivityViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(wallet, Decimal('30000'), 'CASH', 'seed-4')
        refresh_wallet_activity(scope='full')

    def test_wallet_activity_report_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/reporting/wallet-activity/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['topups'], '30000.00')

    def test_missing_school_id_returns_400(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get('/api/v1/reporting/wallet-activity/')
        self.assertEqual(res.status_code, 400)

    def test_cross_tenant_school_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Reporting B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(f'/api/v1/reporting/wallet-activity/?school_id={self.fx["school"].id}')
        # HasRequiredPermission resolves school_id from query params itself and rejects
        # before the view runs — 403, not 404 (same as PeriodGridView's cross-tenant case).
        self.assertEqual(res.status_code, 403)

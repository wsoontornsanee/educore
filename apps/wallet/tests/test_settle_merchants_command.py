import datetime
import io
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import JobRun
from apps.identity.models import Foundation, Person, School, Student
from apps.wallet.models import (
    Merchant,
    MerchantSettlement,
    MerchantSettlementStatus,
    MerchantType,
    POSTerminal,
    POSTransaction,
    POSTransactionStatus,
    Product,
)
from apps.wallet.services import (
    get_or_create_wallet,
    mark_settlement_paid,
    process_pos_transaction,
    settle_merchants_for_school,
    topup_wallet,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


def _second_connection():
    from django.db import connection as default_connection
    from django.db.utils import load_backend
    backend = load_backend(default_connection.settings_dict['ENGINE'])
    return backend.DatabaseWrapper(default_connection.settings_dict, alias='settle_merchants_lock_test_second')


class SettleMerchantsCommandTests(TestCase):
    """Automated test suite for settle_merchants management command and domain service (spec/07 §6 WAL-022, deploy/crontab:30)."""

    def setUp(self):
        # 1. Foundation
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # 2. School
        self.school = School.objects.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="99988877",
            level=School.LEVEL_SMA,
            timezone="Asia/Jakarta",
        )

        # 3. Merchants
        self.merchant_1 = Merchant.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            name="Kantin Bu Siti",
            type=MerchantType.CANTEEN,
            commission_bps=200,  # 2%
            is_active=True,
        )
        self.merchant_2 = Merchant.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            name="Koperasi Siswa",
            type=MerchantType.STATIONERY,
            commission_bps=100,  # 1%
            is_active=True,
        )
        self.merchant_zero = Merchant.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            name="Toko Seragam",
            type=MerchantType.UNIFORM,
            commission_bps=500,  # 5%
            is_active=True,
        )

        # 4. Products & Terminals
        self.prod_1 = Product.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant_1,
            sku="NASI-01",
            name="Nasi Uduk",
            price=Decimal('10000.00'),
        )
        self.term_1 = POSTerminal.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant_1,
            device_id="TERM-M1",
            name="Terminal 1",
        )

        self.prod_2 = Product.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant_2,
            sku="BUKU-01",
            name="Buku Tulis",
            price=Decimal('5000.00'),
        )
        self.term_2 = POSTerminal.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant_2,
            device_id="TERM-M2",
            name="Terminal 2",
        )

        # 5. Student & Wallet
        self.person = Person.objects.create(
            foundation_id=self.foundation.id,
            nik="3171010101010001",
            full_name="Ahmad Santoso",
        )
        self.student = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="2026001",
            status=Student.STATUS_ACTIVE,
        )
        self.wallet = get_or_create_wallet(self.student)
        topup_wallet(self.wallet, Decimal('500000.00'), 'CASH', 'seed-topup-settle')

        # 6. Target Settlement Dates (simulating weekly Sunday run for prior Mon-Sun or Sun-Sat)
        # We will use an anchor date of Sunday 2026-09-20
        self.anchor_date = datetime.date(2026, 9, 20)
        self.period_end = datetime.date(2026, 9, 19)    # Saturday
        self.period_start = datetime.date(2026, 9, 13)  # Sunday (7 days total: 13..19)

        # 7. Seed POS Transactions in target period
        tx1_time = timezone.make_aware(datetime.datetime(2026, 9, 15, 10, 30, 0))
        tx2_time = timezone.make_aware(datetime.datetime(2026, 9, 16, 12, 15, 0))
        tx3_time = timezone.make_aware(datetime.datetime(2026, 9, 17, 9, 0, 0))

        # Merchant 1: 2 transactions (total Rp 20,000 + Rp 30,000 = Rp 50,000)
        process_pos_transaction(
            self.term_1, self.student,
            [{'sku': self.prod_1.sku, 'name': self.prod_1.name, 'qty': 2, 'unit_price': '10000.00'}],
            'tx-m1-01',
            occurred_at=tx1_time,
        )
        process_pos_transaction(
            self.term_1, self.student,
            [{'sku': self.prod_1.sku, 'name': self.prod_1.name, 'qty': 3, 'unit_price': '10000.00'}],
            'tx-m1-02',
            occurred_at=tx2_time,
        )

        # Merchant 2: 1 transaction (total Rp 15,000)
        process_pos_transaction(
            self.term_2, self.student,
            [{'sku': self.prod_2.sku, 'name': self.prod_2.name, 'qty': 3, 'unit_price': '5000.00'}],
            'tx-m2-01',
            occurred_at=tx3_time,
        )

        # Mock GCS storage client for PDF generation
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)

    def test_settle_merchants_for_school_service(self):
        """Service function settle_merchants_for_school processes active merchants and generates statements."""
        res = settle_merchants_for_school(
            school=self.school,
            anchor_date=self.anchor_date,
            days=7,
        )

        self.assertEqual(res['status'], 'COMPLETED')
        self.assertEqual(res['period_start'], '2026-09-13')
        self.assertEqual(res['period_end'], '2026-09-19')
        self.assertEqual(res['total_merchants'], 3)
        self.assertEqual(res['settled'], 3)
        self.assertEqual(res['skipped'], 0)
        self.assertEqual(res['failed'], 0)

        # Total gross = 50,000 + 15,000 + 0 = 65,000
        self.assertEqual(res['total_gross'], Decimal('65000.00'))
        # Merchant 1: 50,000 * 2% = 1,000 commission. Net = 49,000
        # Merchant 2: 15,000 * 1% = 150 commission. Net = 14,850
        # Total commission = 1,150. Total net = 63,850
        self.assertEqual(res['total_commission'], Decimal('1150.00'))
        self.assertEqual(res['total_net'], Decimal('63850.00'))

        # Verify DB records
        s1 = MerchantSettlement.objects.get(merchant=self.merchant_1, period_start=self.period_start)
        self.assertEqual(s1.gross, Decimal('50000.00'))
        self.assertEqual(s1.commission, Decimal('1000.00'))
        self.assertEqual(s1.net, Decimal('49000.00'))
        self.assertEqual(s1.status, MerchantSettlementStatus.PENDING)
        self.assertTrue(s1.statement_pdf_key)

        s2 = MerchantSettlement.objects.get(merchant=self.merchant_2, period_start=self.period_start)
        self.assertEqual(s2.gross, Decimal('15000.00'))
        self.assertEqual(s2.commission, Decimal('150.00'))
        self.assertEqual(s2.net, Decimal('14850.00'))
        self.assertTrue(s2.statement_pdf_key)

        szero = MerchantSettlement.objects.get(merchant=self.merchant_zero, period_start=self.period_start)
        self.assertEqual(szero.gross, Decimal('0.00'))
        self.assertEqual(szero.commission, Decimal('0.00'))
        self.assertEqual(szero.net, Decimal('0.00'))
        self.assertTrue(szero.statement_pdf_key)

    def test_management_command_full_sweep_and_job_run(self):
        """Management command settle_merchants executes cleanly and records JobRun (ARC-008, ARC-013)."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            f'--anchor-date={self.anchor_date.isoformat()}',
            f'--school-id={self.school.id}',
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Starting merchant settlement sweep", output)
        self.assertIn("Merchant settlement sweep complete: 3 settled, 0 skipped, 0 failed", output)
        self.assertIn("Total Gross: Rp 65000.00", output)

        # Verify JobRun entry
        job_run = JobRun.objects.filter(job_name='settle_merchants').latest('id')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 3)
        self.assertIsNotNone(job_run.finished_at)

    def test_rerun_while_pending_updates_in_place(self):
        """Re-running settlement while still PENDING updates in place without duplicates (WAL-022)."""
        # First run
        settle_merchants_for_school(self.school, anchor_date=self.anchor_date)
        count_before = MerchantSettlement.objects.count()
        self.assertEqual(count_before, 3)
        s1_initial = MerchantSettlement.objects.get(merchant=self.merchant_1, period_start=self.period_start)

        # Add a new completed transaction within the period
        tx_extra = timezone.make_aware(datetime.datetime(2026, 9, 18, 14, 0, 0))
        process_pos_transaction(
            self.term_1, self.student,
            [{'sku': self.prod_1.sku, 'name': self.prod_1.name, 'qty': 1, 'unit_price': '10000.00'}],
            'tx-m1-extra',
            occurred_at=tx_extra,
        )

        # Re-run settlement
        res2 = settle_merchants_for_school(self.school, anchor_date=self.anchor_date)
        self.assertEqual(res2['settled'], 3)
        self.assertEqual(MerchantSettlement.objects.count(), count_before)

        s1_updated = MerchantSettlement.objects.get(merchant=self.merchant_1, period_start=self.period_start)
        self.assertEqual(s1_updated.id, s1_initial.id)
        # Gross was 50,000, now 60,000. Commission: 1,200. Net: 58,800.
        self.assertEqual(s1_updated.gross, Decimal('60000.00'))
        self.assertEqual(s1_updated.commission, Decimal('1200.00'))
        self.assertEqual(s1_updated.net, Decimal('58800.00'))

    def test_already_paid_settlement_skipped_gracefully(self):
        """Merchants with already PAID settlements for the period are skipped without aborting the batch."""
        # Pre-create and mark merchant 1 as PAID
        res1 = settle_merchants_for_school(
            school=self.school,
            anchor_date=self.anchor_date,
            merchant_id=str(self.merchant_1.id),
        )
        s1 = MerchantSettlement.objects.get(merchant=self.merchant_1, period_start=self.period_start)
        mark_settlement_paid(s1)
        self.assertEqual(s1.status, MerchantSettlementStatus.PAID)

        # Now run settlement for all merchants in the school
        res = settle_merchants_for_school(
            school=self.school,
            anchor_date=self.anchor_date,
        )

        self.assertEqual(res['settled'], 2)  # merchant_2 and merchant_zero settled
        self.assertEqual(res['skipped'], 1)  # merchant_1 skipped
        self.assertEqual(res['failed'], 0)

        skipped_items = [r for r in res['results'] if r['status'] == 'SKIPPED_ALREADY_PAID']
        self.assertEqual(len(skipped_items), 1)
        self.assertEqual(skipped_items[0]['merchant_id'], str(self.merchant_1.id))

    def test_dry_run_mode(self):
        """--dry-run previews financial totals and transaction counts without modifying DB."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            f'--anchor-date={self.anchor_date.isoformat()}',
            f'--school-id={self.school.id}',
            '--dry-run',
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("dry_run=True", output)
        self.assertIn("Gross: Rp 65000.00", output)

        # Assert no settlements created
        self.assertEqual(MerchantSettlement.objects.count(), 0)
        # Assert no JobRun created in dry run
        self.assertEqual(JobRun.objects.filter(job_name='settle_merchants').count(), 0)

    def test_skip_zero_sales_flag(self):
        """--skip-zero-sales excludes merchants with 0 completed transactions."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            f'--anchor-date={self.anchor_date.isoformat()}',
            f'--school-id={self.school.id}',
            '--skip-zero-sales',
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("SKIPPED_ZERO_SALES", output)
        self.assertIn("2 settled, 1 skipped", output)

        self.assertTrue(MerchantSettlement.objects.filter(merchant=self.merchant_1).exists())
        self.assertTrue(MerchantSettlement.objects.filter(merchant=self.merchant_2).exists())
        self.assertFalse(MerchantSettlement.objects.filter(merchant=self.merchant_zero).exists())

    def test_custom_period_flags(self):
        """Explicit --period-start and --period-end override default weekly window."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            '--period-start=2026-09-14',
            '--period-end=2026-09-16',
            f'--school-id={self.school.id}',
            stdout=out,
        )

        self.assertIn("Period: 2026-09-14..2026-09-16", out.getvalue())
        s1 = MerchantSettlement.objects.get(merchant=self.merchant_1)
        self.assertEqual(s1.period_start, datetime.date(2026, 9, 14))
        self.assertEqual(s1.period_end, datetime.date(2026, 9, 16))

    def test_invalid_period_dates(self):
        """Command rejects start date after end date and invalid format."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            '--period-start=2026-09-20',
            '--period-end=2026-09-10',
            stdout=out,
        )
        self.assertIn("Invalid period: start", out.getvalue())

        out_invalid = io.StringIO()
        call_command(
            'settle_merchants',
            '--period-start=not-a-date',
            stdout=out_invalid,
        )
        self.assertIn("Invalid date format", out_invalid.getvalue())

    def test_advisory_lock_contention(self):
        """Command exits cleanly with warning when advisory lock is already held (ARC-007)."""
        from apps.core.locks import acquire_advisory_lock, release_advisory_lock

        conn2 = _second_connection()
        try:
            acquired = acquire_advisory_lock('settle_merchants', db_connection=conn2)
            self.assertTrue(acquired)
            out = io.StringIO()
            call_command('settle_merchants', stdout=out)
            output = out.getvalue()
            self.assertIn("already held. Exiting immediately.", output)
        finally:
            release_advisory_lock('settle_merchants', db_connection=conn2)
            conn2.close()

    def test_cron_host_command_guard(self):
        """Command refuses to run without EDUCORE_CRON_HOST=1 when enforcement is active (ARC-013)."""
        from django.core.management.base import CommandError
        from django.test import override_settings

        with override_settings(EDUCORE_CRON_HOST_ENFORCED=True):
            with mock.patch.dict('os.environ', {'EDUCORE_CRON_HOST': '0'}, clear=False):
                with self.assertRaises(CommandError) as ctx:
                    call_command('settle_merchants')
                self.assertIn("EDUCORE_CRON_HOST=1 not set", str(ctx.exception))

            # Passing --force bypasses the guard
            out = io.StringIO()
            with mock.patch.dict('os.environ', {'EDUCORE_CRON_HOST': '0'}, clear=False):
                call_command('settle_merchants', '--force', '--dry-run', stdout=out)
                self.assertIn("dry_run=True", out.getvalue())

    def test_merchant_and_school_filtering(self):
        """Filtering by merchant-id settles only that merchant."""
        out = io.StringIO()
        call_command(
            'settle_merchants',
            f'--anchor-date={self.anchor_date.isoformat()}',
            f'--merchant-id={self.merchant_1.id}',
            stdout=out,
        )
        self.assertIn("1 settled", out.getvalue())
        self.assertEqual(MerchantSettlement.objects.count(), 1)
        self.assertEqual(MerchantSettlement.objects.first().merchant, self.merchant_1)

    def test_multi_tenancy_isolation(self):
        """Cross-tenant merchants are isolated and not settled under another foundation context."""
        f2 = Foundation.objects.create(
            legal_name="Yayasan Bintang Gemilang",
            brand_name="Bintang Gemilang",
            status=Foundation.STATUS_ACTIVE,
        )
        s2 = School.objects.create(
            foundation_id=f2.id,
            name="SMA Bintang Gemilang",
            npsn="11122233",
            level=School.LEVEL_SMA,
        )
        m_f2 = Merchant.all_tenants.create(
            foundation_id=f2.id,
            school=s2,
            name="Kantin F2",
            type=MerchantType.CANTEEN,
            is_active=True,
        )

        out = io.StringIO()
        # Settle only Foundation 1
        call_command(
            'settle_merchants',
            f'--anchor-date={self.anchor_date.isoformat()}',
            f'--foundation-id={self.foundation.id}',
            stdout=out,
        )

        self.assertFalse(MerchantSettlement.all_tenants.filter(merchant=m_f2).exists())
        self.assertEqual(MerchantSettlement.all_tenants.filter(foundation_id=self.foundation.id).count(), 3)

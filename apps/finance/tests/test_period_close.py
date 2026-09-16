import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import AuditEvent, DomainEvent
from apps.finance.models import (
    AccountCode,
    FiscalPeriod,
    FiscalPeriodStatus,
    LedgerEntry,
    LedgerJournal,
)
from apps.finance.services.ledger import post_ledger_journal
from apps.finance.services.period_close import (
    PeriodClosedError,
    PeriodCloseValidationError,
    close_fiscal_period,
    get_effective_posting_datetime,
    get_next_open_period,
    get_next_period_str,
    get_period_date_range,
    is_period_closed,
    reopen_fiscal_period,
    validate_period_format,
)
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class FiscalPeriodServiceTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cita",
            brand_name="Insan Cita",
            npwp="01.222.333.4-555.000",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Insan Cita",
            npsn="20200001",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        self.user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="admin@insancita.sch.id",
            full_name="Admin Insan Cita",
        )

    def test_validate_period_format(self):
        validate_period_format("2026-01")
        validate_period_format("2026-12")
        with self.assertRaises(ValueError):
            validate_period_format("2026-13")
        with self.assertRaises(ValueError):
            validate_period_format("2026-0")
        with self.assertRaises(ValueError):
            validate_period_format("invalid")
        with self.assertRaises(ValueError):
            validate_period_format("")

    def test_period_date_range_and_next_period(self):
        start, end = get_period_date_range("2026-02")
        self.assertEqual(start, datetime.date(2026, 2, 1))
        self.assertEqual(end, datetime.date(2026, 2, 28))

        self.assertEqual(get_next_period_str("2026-02"), "2026-03")
        self.assertEqual(get_next_period_str("2026-12"), "2027-01")

    def test_close_empty_period_succeeds(self):
        """A period with 0 entries has 0 Dr / 0 Cr and can be closed."""
        period = "2026-05"
        fp = close_fiscal_period(self.school, period, closed_by=self.user, notes="Closing empty period")
        self.assertEqual(fp.status, FiscalPeriodStatus.CLOSED)
        self.assertEqual(fp.total_journals, 0)
        self.assertEqual(fp.total_debit, Decimal('0.00'))
        self.assertEqual(fp.total_credit, Decimal('0.00'))
        self.assertTrue(is_period_closed(self.school, period))

        # Assert AuditEvent & DomainEvent
        self.assertTrue(AuditEvent.objects.filter(action="PERIOD_CLOSED", entity_id=str(fp.id)).exists())
        self.assertTrue(DomainEvent.objects.filter(name="finance.period_closed").exists())

    def test_close_balanced_period_aggregates_totals(self):
        period = "2026-06"
        dt = timezone.make_aware(datetime.datetime(2026, 6, 15, 10, 0, 0))

        # Post balanced journal
        post_ledger_journal(
            school=self.school,
            ref_type="TEST",
            ref_id=1,
            description="Test June Journal",
            currency="IDR",
            entries=[
                {'account_code': AccountCode.CASH_BANK, 'debit': Decimal('500000.00'), 'credit': Decimal('0.00')},
                {'account_code': AccountCode.ACCOUNTS_RECEIVABLE, 'debit': Decimal('0.00'), 'credit': Decimal('500000.00')},
            ],
            occurred_at=dt,
        )

        fp = close_fiscal_period(self.school, period, closed_by=self.user)
        self.assertEqual(fp.status, FiscalPeriodStatus.CLOSED)
        self.assertEqual(fp.total_journals, 1)
        self.assertEqual(fp.total_debit, Decimal('500000.00'))
        self.assertEqual(fp.total_credit, Decimal('500000.00'))

    def test_close_unbalanced_period_fails(self):
        period = "2026-07"
        dt = timezone.make_aware(datetime.datetime(2026, 7, 10, 10, 0, 0))

        journal = LedgerJournal.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            number="JRN/SD/2026/07/001",
            ref_type="TEST",
            ref_id=2,
            description="Unbalanced raw journal",
            currency="IDR",
            occurred_at=dt,
        )
        LedgerEntry.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            journal=journal,
            account_code=AccountCode.CASH_BANK,
            debit=Decimal('100000.00'),
            credit=Decimal('0.00'),
            currency="IDR",
            occurred_at=dt,
        )
        # Missing credit entry -> unbalanced

        with self.assertRaises(PeriodCloseValidationError):
            close_fiscal_period(self.school, period, closed_by=self.user)

        self.assertFalse(is_period_closed(self.school, period))

    def test_ledger_locking_rejects_backdated_postings(self):
        """FIN-025: Closing a month locks ledger entries for that period."""
        period = "2026-08"
        close_fiscal_period(self.school, period, closed_by=self.user)
        self.assertTrue(is_period_closed(self.school, period))

        backdated_dt = timezone.make_aware(datetime.datetime(2026, 8, 20, 14, 0, 0))

        with self.assertRaises(PeriodClosedError):
            post_ledger_journal(
                school=self.school,
                ref_type="TEST",
                ref_id=10,
                description="Backdated posting",
                currency="IDR",
                entries=[
                    {'account_code': AccountCode.CASH_BANK, 'debit': Decimal('200000.00'), 'credit': Decimal('0.00')},
                    {'account_code': AccountCode.ACCOUNTS_RECEIVABLE, 'debit': Decimal('0.00'), 'credit': Decimal('200000.00')},
                ],
                occurred_at=backdated_dt,
                allow_next_period_routing=False,
            )

    def test_post_close_correction_routes_to_next_open_period(self):
        """FIN-025: Post-close corrections route to the next open period."""
        close_fiscal_period(self.school, "2026-08", closed_by=self.user)
        close_fiscal_period(self.school, "2026-09", closed_by=self.user)
        # 2026-10 is OPEN

        backdated_dt = timezone.make_aware(datetime.datetime(2026, 8, 15, 12, 0, 0))
        routed_dt = get_effective_posting_datetime(self.school, backdated_dt, allow_routing=True)

        self.assertEqual(routed_dt.strftime('%Y-%m'), "2026-10")
        self.assertEqual(routed_dt.day, 1)

        # Execute journal posting with allow_next_period_routing=True
        journal = post_ledger_journal(
            school=self.school,
            ref_type="CORRECTION",
            ref_id=99,
            description="Post-close adjustment",
            currency="IDR",
            entries=[
                {'account_code': AccountCode.CASH_BANK, 'debit': Decimal('150000.00'), 'credit': Decimal('0.00')},
                {'account_code': AccountCode.ACCOUNTS_RECEIVABLE, 'debit': Decimal('0.00'), 'credit': Decimal('150000.00')},
            ],
            occurred_at=backdated_dt,
            allow_next_period_routing=True,
        )
        self.assertEqual(journal.occurred_at.strftime('%Y-%m'), "2026-10")

    def test_reopen_fiscal_period(self):
        period = "2026-04"
        close_fiscal_period(self.school, period, closed_by=self.user)
        self.assertTrue(is_period_closed(self.school, period))

        # Reopen without reason fails
        with self.assertRaises(ValueError):
            reopen_fiscal_period(self.school, period, reopened_by=self.user, reason="")

        fp = reopen_fiscal_period(
            self.school,
            period,
            reopened_by=self.user,
            reason="Koreksi data pembayaran offline",
        )
        self.assertEqual(fp.status, FiscalPeriodStatus.OPEN)
        self.assertFalse(is_period_closed(self.school, period))

        # Verify audit logging
        self.assertTrue(AuditEvent.objects.filter(action="PERIOD_REOPENED", entity_id=str(fp.id)).exists())
        self.assertTrue(DomainEvent.objects.filter(name="finance.period_reopened").exists())

        # Now backdated posting succeeds
        dt = timezone.make_aware(datetime.datetime(2026, 4, 10, 9, 0, 0))
        journal = post_ledger_journal(
            school=self.school,
            ref_type="TEST",
            ref_id=12,
            description="Reopened posting",
            currency="IDR",
            entries=[
                {'account_code': AccountCode.CASH_BANK, 'debit': Decimal('100000.00'), 'credit': Decimal('0.00')},
                {'account_code': AccountCode.ACCOUNTS_RECEIVABLE, 'debit': Decimal('0.00'), 'credit': Decimal('100000.00')},
            ],
            occurred_at=dt,
        )
        self.assertIsNotNone(journal)


class FiscalPeriodAPITests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Tunas Bangsa",
            brand_name="Tunas Bangsa",
            npwp="01.555.444.3-222.000",
            address="Surabaya",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Tunas Bangsa",
            npsn="20500001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281999999999",
            email="bendahara@tunasbangsa.sch.id",
            full_name="Bendahara Tunas Bangsa",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281888888888",
            email="parent@tunasbangsa.sch.id",
            full_name="Parent User",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.parent_user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # Other foundation for tenant isolation test
        self.other_fnd = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Lain",
            npwp="01.111.111.1-111.000",
            address="Jakarta",
        )
        self.other_school = School.all_tenants.create(
            foundation_id=self.other_fnd.id,
            name="SMA Lain",
            npsn="20599999",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

    def test_list_and_filter_periods(self):
        self.client.force_authenticate(user=self.finance_user)
        close_fiscal_period(self.school, "2026-01")
        close_fiscal_period(self.school, "2026-02")

        resp = self.client.get('/api/v1/finance/periods/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        self.assertEqual(len(results), 2)

        # Filter by period
        resp_filtered = self.client.get('/api/v1/finance/periods/?period=2026-01')
        results_filtered = resp_filtered.data.get('results', resp_filtered.data)
        self.assertEqual(len(results_filtered), 1)
        self.assertEqual(results_filtered[0]['period'], "2026-01")

    def test_close_period_via_endpoint_path(self):
        self.client.force_authenticate(user=self.finance_user)
        payload = {
            'school_id': self.school.id,
            'notes': "Close via URL path",
        }
        resp = self.client.post('/api/v1/finance/periods/2026-03/close/', data=payload)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['period'], "2026-03")
        self.assertEqual(resp.data['status'], FiscalPeriodStatus.CLOSED)
        self.assertTrue(is_period_closed(self.school, "2026-03"))

    def test_reopen_period_via_endpoint(self):
        self.client.force_authenticate(user=self.finance_user)
        close_fiscal_period(self.school, "2026-03")

        payload = {
            'school_id': self.school.id,
            'reason': "Perbaikan entri jurnal",
        }
        resp = self.client.post('/api/v1/finance/periods/2026-03/reopen/', data=payload)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], FiscalPeriodStatus.OPEN)
        self.assertFalse(is_period_closed(self.school, "2026-03"))

    def test_rbac_rejects_unauthorized_user(self):
        self.client.force_authenticate(user=self.parent_user)
        payload = {
            'school_id': self.school.id,
            'notes': "Unauthorized attempt",
        }
        resp = self.client.post('/api/v1/finance/periods/2026-03/close/', data=payload)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_tenant_isolation(self):
        """User from Foundation A cannot close period of School from Foundation B."""
        self.client.force_authenticate(user=self.finance_user)
        payload = {
            'school_id': self.other_school.id,
            'notes': "Cross tenant attempt",
        }
        resp = self.client.post('/api/v1/finance/periods/2026-03/close/', data=payload)
        # Returns 404 since other_school is not in user's foundation context
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class FiscalPeriodCronCommandTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bintang Cemerlang",
            brand_name="Bintang Cemerlang",
            npwp="01.333.444.5-666.000",
            address="Semarang",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Bintang Cemerlang",
            npsn="20400001",
            level=School.LEVEL_SMP,
            base_currency="IDR",
        )

    def test_cron_command_dry_run(self):
        call_command('close_fiscal_periods', '--period=2026-05', '--dry-run', '--force')
        self.assertFalse(is_period_closed(self.school, "2026-05"))

    def test_cron_command_execution(self):
        call_command('close_fiscal_periods', '--period=2026-05', '--school-id', str(self.school.id), '--force')
        self.assertTrue(is_period_closed(self.school, "2026-05"))

        # Idempotent re-run
        call_command('close_fiscal_periods', '--period=2026-05', '--school-id', str(self.school.id), '--force')
        self.assertTrue(is_period_closed(self.school, "2026-05"))

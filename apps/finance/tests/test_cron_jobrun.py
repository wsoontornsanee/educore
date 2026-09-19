"""The four money-moving cron commands each leave one JobRun per real run (spec/01 ARC-008)."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import JobRun
from apps.finance.tests.test_bank_sftp_pull import build_fixture
from apps.finance.services import set_bank_sftp_config


def run_command(*args):
    call_command(*args, '--force', stdout=StringIO())


class _Base(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def rows(self, job_name):
        return JobRun.objects.filter(job_name=job_name)


class GenerateInvoicesJobRunTests(_Base):
    def test_success_run_leaves_one_success_row(self):
        run_command('generate_invoices', '--period=2026-11')
        row = self.rows('generate_invoices').get()
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertIsNotNone(row.finished_at)

    def test_items_processed_counts_generated_invoices(self):
        result = {'generated_count': 4, 'skipped_existing_count': 1, 'currency': 'IDR', 'total_net_amount': '0.00'}
        with patch('apps.finance.management.commands.generate_invoices.generate_monthly_invoices', return_value=result):
            run_command('generate_invoices', '--period=2026-11')
        self.assertEqual(self.rows('generate_invoices').get().items_processed, 4)

    def test_dry_run_writes_no_row(self):
        run_command('generate_invoices', '--period=2026-11', '--dry-run')
        self.assertFalse(self.rows('generate_invoices').exists())

    def test_failure_is_recorded_and_still_raises(self):
        with patch('apps.finance.management.commands.generate_invoices.generate_monthly_invoices', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                run_command('generate_invoices', '--period=2026-11')
        row = self.rows('generate_invoices').get()
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertIn('boom', row.error_text)


class RunArrearsLadderJobRunTests(_Base):
    def test_success_run_counts_dispatched_reminders(self):
        result = {'evaluated_count': 9, 'eligible_count': 5, 'dispatched_count': 3, 'skipped_existing_count': 2}
        with patch('apps.finance.management.commands.run_arrears_ladder.run_arrears_ladder', return_value=result):
            run_command('run_arrears_ladder')
        row = self.rows('run_arrears_ladder').get()
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 3)

    def test_dry_run_writes_no_row(self):
        run_command('run_arrears_ladder', '--dry-run')
        self.assertFalse(self.rows('run_arrears_ladder').exists())

    def test_bad_date_is_a_usage_error_not_a_failed_run(self):
        run_command('run_arrears_ladder', '--as-of-date=not-a-date')
        self.assertFalse(self.rows('run_arrears_ladder').exists())

    def test_failure_is_recorded_and_still_raises(self):
        with patch('apps.finance.management.commands.run_arrears_ladder.run_arrears_ladder', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                run_command('run_arrears_ladder')
        self.assertEqual(self.rows('run_arrears_ladder').get().status, JobRun.STATUS_FAILED)


class ReconcilePaymentsJobRunTests(_Base):
    OK = {'total': 3, 'matched': 3, 'missing': 0, 'mismatch': 0}

    def test_success_run_counts_reconciled_records(self):
        with patch('apps.finance.management.commands.reconcile_payments.reconcile_gateway_settlement', return_value=self.OK):
            run_command('reconcile_payments', '--provider=XENDIT')
        row = self.rows('reconcile_payments').get()
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 3)

    def test_missing_and_mismatched_payments_are_findings_not_a_failed_run(self):
        found = {'total': 5, 'matched': 3, 'missing': 1, 'mismatch': 1}
        with patch('apps.finance.management.commands.reconcile_payments.reconcile_gateway_settlement', return_value=found):
            run_command('reconcile_payments', '--provider=XENDIT')
        self.assertEqual(self.rows('reconcile_payments').get().status, JobRun.STATUS_SUCCESS)

    def test_provider_error_marks_the_run_failed_with_the_reason(self):
        bad = {'total': 0, 'matched': 0, 'missing': 0, 'mismatch': 0, 'error': 'gateway 503'}
        with patch('apps.finance.management.commands.reconcile_payments.reconcile_gateway_settlement', return_value=bad):
            run_command('reconcile_payments', '--provider=XENDIT')
        row = self.rows('reconcile_payments').get()
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertIn('gateway 503', row.error_text)
        self.assertIn('XENDIT', row.error_text)

    def test_dry_run_writes_no_row(self):
        with patch('apps.finance.management.commands.reconcile_payments.reconcile_gateway_settlement', return_value=self.OK):
            run_command('reconcile_payments', '--provider=XENDIT', '--dry-run')
        self.assertFalse(self.rows('reconcile_payments').exists())

    def test_bad_date_is_a_usage_error_not_a_failed_run(self):
        run_command('reconcile_payments', '--date=nonsense')
        self.assertFalse(self.rows('reconcile_payments').exists())

    def test_exception_is_recorded_and_still_raises(self):
        with patch('apps.finance.management.commands.reconcile_payments.reconcile_gateway_settlement', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                run_command('reconcile_payments', '--provider=XENDIT')
        self.assertEqual(self.rows('reconcile_payments').get().status, JobRun.STATUS_FAILED)


class PullBankStatementsJobRunTests(_Base):
    def test_no_active_configs_is_a_clean_success(self):
        run_command('pull_bank_statements', '--date=2026-09-14')
        row = self.rows('pull_bank_statements').get()
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 0)

    def test_stub_failures_end_the_run_failed_and_name_each_bank(self):
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id')
        set_bank_sftp_config(self.fx['school'], bank_code='MANDIRI', host='sftp.mandiri.co.id')
        run_command('pull_bank_statements', '--date=2026-09-14')
        row = self.rows('pull_bank_statements').get()
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertIn('BCA', row.error_text)
        self.assertIn('MANDIRI', row.error_text)

    def test_bad_date_is_a_usage_error_not_a_failed_run(self):
        run_command('pull_bank_statements', '--date=nonsense')
        self.assertFalse(self.rows('pull_bank_statements').exists())


class CloseFiscalPeriodsJobRunTests(_Base):
    def test_success_run_counts_closed_periods(self):
        run_command('close_fiscal_periods', '--period=2026-08')
        row = self.rows('close_fiscal_periods').get()
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 1)

    def test_dry_run_writes_no_row(self):
        run_command('close_fiscal_periods', '--period=2026-08', '--dry-run')
        self.assertFalse(self.rows('close_fiscal_periods').exists())

    def test_a_period_that_fails_to_close_ends_the_run_failed_with_the_reason(self):
        from apps.finance.services.period_close import PeriodCloseValidationError
        with patch(
            'apps.finance.management.commands.close_fiscal_periods.close_fiscal_period',
            side_effect=PeriodCloseValidationError('ledger tidak seimbang'),
        ):
            run_command('close_fiscal_periods', '--period=2026-08')
        row = self.rows('close_fiscal_periods').get()
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertIn('ledger tidak seimbang', row.error_text)
        self.assertEqual(row.items_processed, 0)

"""Read-only data-repair report: SETTLED payments that never reached the books
(the old status-only reconciliation paths), and its management command."""
import csv
import io
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone

from apps.finance.models import (
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentStatus,
)
from apps.finance.services.reconciliation import find_settled_payments_missing_books
from apps.finance.tests.test_reconciliation_manual_settle import ReconFixture
from apps.identity.models import Foundation


class ReportFixture(ReconFixture):
    def _status_only_settled(self, external_id, amount=Decimal('100000.00')):
        """What the old reconciliation paths left behind: SETTLED, nothing else."""
        payment = self._payment(external_id, amount, status=PaymentStatus.SETTLED)
        Payment.all_tenants.filter(pk=payment.pk).update(settled_at=timezone.now())
        return payment

    def flags_by_reference(self, **kwargs):
        return {row['reference']: row['flags'] for row in find_settled_payments_missing_books(**kwargs)}


class UnbookedSettledPaymentsTests(ReportFixture):

    def test_status_only_settlement_is_flagged_on_every_count(self):
        payment = self._status_only_settled('EXT-OLD')
        self.assertEqual(
            self.flags_by_reference(foundation_id=self.foundation.id)[payment.reference],
            ['NO_JOURNAL', 'NO_ALLOCATION', 'NO_RECEIPT'],
        )

    def test_a_properly_settled_payment_is_not_flagged(self):
        discrepancy = self._discrepancy()  # linked to self.payment (PENDING)
        self._resolve(discrepancy)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SETTLED)
        self.assertNotIn(self.payment.reference, self.flags_by_reference(foundation_id=self.foundation.id))

    def test_journal_without_allocation_is_a_review_flag_not_a_missing_journal(self):
        """A pure overpayment is credited without touching an invoice: journal yes, allocation no."""
        payment = self._status_only_settled('EXT-CREDIT')
        Payment.all_tenants.filter(pk=payment.pk).update(receipt_number='RCP/TEST/1')
        LedgerJournal.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, number='JRN/TEST/1',
            description='Kredit', ref_type='PAYMENT', ref_id=str(payment.id),
        )
        self.assertEqual(self.flags_by_reference(foundation_id=self.foundation.id)[payment.reference],
                         ['NO_ALLOCATION'])

    def test_pending_and_soft_deleted_payments_and_other_foundations_are_left_out(self):
        pending = self._payment('EXT-PENDING', Decimal('1.00'))  # PENDING, not settled
        deleted = self._status_only_settled('EXT-DELETED')
        Payment.all_tenants.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        other = Foundation.objects.create(legal_name='Yayasan Lain', brand_name='Lain')
        elsewhere = self._status_only_settled('EXT-ELSEWHERE')
        Payment.all_tenants.filter(pk=elsewhere.pk).update(foundation_id=other.id)
        flagged = self.flags_by_reference(foundation_id=self.foundation.id)
        self.assertNotIn(pending.reference, flagged)
        self.assertNotIn(deleted.reference, flagged)
        self.assertNotIn(elsewhere.reference, flagged)
        self.assertIn(elsewhere.reference, self.flags_by_reference(foundation_id=other.id))
        self.assertIn(elsewhere.reference, self.flags_by_reference())  # unfiltered = every foundation

    def test_school_filter(self):
        self._status_only_settled('EXT-OLD')
        self.assertTrue(self.flags_by_reference(school_id=self.school.id))
        self.assertFalse(self.flags_by_reference(school_id=self.school.id + 999))


class ReportCommandTests(ReportFixture):
    def _run(self, **options):
        out, err = io.StringIO(), io.StringIO()
        call_command('report_unbooked_settled_payments', stdout=out, stderr=err, **options)
        return out.getvalue(), err.getvalue()

    def test_command_reports_counts_and_never_writes(self):
        self._status_only_settled('EXT-OLD')
        before = (
            Payment.all_tenants.count(), LedgerJournal.all_tenants.count(), PaymentAllocation.all_tenants.count(),
            list(Payment.all_tenants.values_list('id', 'status', 'receipt_number', 'settled_at')),
        )
        out, err = self._run(foundation_id=self.foundation.id)
        after = (
            Payment.all_tenants.count(), LedgerJournal.all_tenants.count(), PaymentAllocation.all_tenants.count(),
            list(Payment.all_tenants.values_list('id', 'status', 'receipt_number', 'settled_at')),
        )
        self.assertEqual(before, after)
        self.assertIn('PAY/20100077/2026/EXT-OLD', out)
        self.assertIn('flags=NO_JOURNAL,NO_ALLOCATION,NO_RECEIPT', out)
        self.assertIn('1 settled payment(s) flagged: 1 with no ledger journal', out)
        self.assertIn('Nothing was changed', out)
        self.assertIn('never writes', err)
        self.assertNotIn('Ani', out)  # no student name / PII in the report

    def test_command_with_nothing_flagged(self):
        out, err = self._run(foundation_id=self.foundation.id)
        self.assertIn('0 settled payment(s) flagged', out)
        self.assertEqual(err, '')

    def test_csv_export(self):
        self._status_only_settled('EXT-OLD')
        with tempfile.NamedTemporaryFile('r', suffix='.csv', delete=False) as handle:
            path = handle.name
        self._run(foundation_id=self.foundation.id, csv_path=path)
        with open(path, newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reference'], 'PAY/20100077/2026/EXT-OLD')
        self.assertEqual(rows[0]['flags'], 'NO_JOURNAL NO_ALLOCATION NO_RECEIPT')
        self.assertNotIn('student_name', rows[0])

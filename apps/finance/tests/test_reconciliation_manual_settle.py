"""Reconciliation settlements (manual resolution and the cron's auto-settle) must
reach AR and the ledger.

Both used to flip Payment.status to SETTLED and stop: the money never got a
receipt, an invoice allocation or a ledger journal. They now run the same
settlement path a gateway webhook does.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.finance.models import (
    DiscrepancyResolution, DiscrepancyType, GatewaySettlementBatch, Invoice, InvoiceStatus, LedgerEntry,
    LedgerJournal, Payment, PaymentAllocation, PaymentDiscrepancy, PaymentMethod, PaymentStatus,
    SettlementBatchStatus,
)
from apps.finance.services.reconciliation import reconcile_gateway_settlement, resolve_discrepancy
from apps.identity.models import Foundation, Person, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ReconFixture(TestCase):
    """Foundation, school, student, one ISSUED invoice, one PENDING payment and a batch."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(legal_name='Yayasan Rekon', brand_name='Rekon', npwp='01.777.666.5-444.000')
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD Rekon', npsn='20100077', level=School.LEVEL_SD, base_currency='IDR',
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Ani', nik='3171010101010077')
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person, nis='77', nisn='0077777777',
            status=Student.STATUS_ACTIVE,
        )
        self.user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000077', email='rekon@test.id', full_name='Bendahara',
        )
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number='INV/20100077/2026/000001', period='2026-10', due_date='2026-10-10',
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), currency='IDR', status=InvoiceStatus.ISSUED,
        )
        self.payment = self._payment('EXT-1', Decimal('500000.00'))
        self.batch = GatewaySettlementBatch(
            foundation_id=self.foundation.id, provider='MOCK', settlement_date=datetime.date(2026, 9, 14),
            status=SettlementBatchStatus.PARTIAL,
        )
        self.batch.save()

    def tearDown(self):
        clear_current_foundation_id()

    def _payment(self, external_id, amount, status=PaymentStatus.PENDING):
        return Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student, amount=amount,
            fee=Decimal('0.00'), net=amount, currency='IDR', method=PaymentMethod.VA, channel='BCA_VA',
            reference=f'PAY/20100077/2026/{external_id}', external_id=external_id, status=status,
            paid_at=timezone.now(),
        )

    def _discrepancy(self, payment=None, gateway_amount=Decimal('500000.00'), gateway_fee=Decimal('3000.00'),
                     kind=DiscrepancyType.MISSING_IN_SYSTEM):
        payment = payment or self.payment
        discrepancy = PaymentDiscrepancy(
            foundation_id=self.foundation.id, batch=self.batch, payment=payment, external_id=payment.external_id,
            discrepancy_type=kind, gateway_amount=gateway_amount, gateway_fee=gateway_fee,
            gateway_net=gateway_amount - gateway_fee, system_amount=payment.amount,
            resolution=DiscrepancyResolution.PENDING,
        )
        discrepancy.save()
        return discrepancy

    def _resolve(self, discrepancy, resolution=DiscrepancyResolution.MANUAL_SETTLED):
        return resolve_discrepancy(
            discrepancy_id=discrepancy.id, resolution=resolution, resolved_by=self.user,
            foundation_id=self.foundation.id, notes='Cocok dengan mutasi bank',
        )

    def _journal_totals(self, payment):
        journal = LedgerJournal.objects.get(ref_type='PAYMENT', ref_id=str(payment.id))
        totals = LedgerEntry.objects.filter(journal=journal).aggregate(debit=Sum('debit'), credit=Sum('credit'))
        return totals['debit'], totals['credit']


class ManualSettleTests(ReconFixture):
    def test_manual_settle_allocates_to_invoice_and_posts_a_balanced_journal(self):
        self._resolve(self._discrepancy())
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SETTLED)
        self.assertIsNotNone(self.payment.settled_at)
        self.assertTrue(self.payment.receipt_number)
        allocation = PaymentAllocation.objects.get(payment=self.payment)
        self.assertEqual(allocation.invoice_id, self.invoice.id)
        self.assertEqual(allocation.amount, Decimal('500000.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PARTIALLY_PAID)
        debit, credit = self._journal_totals(self.payment)
        self.assertEqual(debit, credit)
        self.assertEqual(credit, Decimal('500000.00'))

    def test_gateway_fee_is_kept_and_net_derived_so_the_journal_balances(self):
        self._resolve(self._discrepancy(gateway_fee=Decimal('3000.00')))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.fee, Decimal('3000.00'))
        self.assertEqual(self.payment.net, Decimal('497000.00'))
        debit, credit = self._journal_totals(self.payment)
        self.assertEqual(debit, credit)

    def test_amount_mismatch_settles_at_the_recorded_system_amount(self):
        discrepancy = self._discrepancy(
            gateway_amount=Decimal('490000.00'), gateway_fee=Decimal('3000.00'), kind=DiscrepancyType.AMOUNT_MISMATCH,
        )
        self._resolve(discrepancy)
        self.assertEqual(PaymentAllocation.objects.get(payment=self.payment).amount, Decimal('500000.00'))
        debit, credit = self._journal_totals(self.payment)
        self.assertEqual(debit, credit)

    def test_records_who_settled_it_in_the_audit_trail(self):
        self._resolve(self._discrepancy())
        event = AuditEvent.objects.get(action='finance.payment.settled', entity_id=str(self.payment.id))
        self.assertEqual(event.actor_id, str(self.user.id))
        self.assertEqual(event.role, 'MANUAL_RECONCILIATION')

    def test_already_settled_payment_is_not_settled_twice(self):
        self.payment.status = PaymentStatus.SETTLED
        self.payment.save(update_fields=['status'])
        discrepancy = self._discrepancy()
        result = self._resolve(discrepancy)
        self.assertEqual(result.resolution, DiscrepancyResolution.MANUAL_SETTLED)
        self.assertFalse(PaymentAllocation.objects.filter(payment=self.payment).exists())
        self.assertFalse(LedgerJournal.objects.filter(ref_type='PAYMENT', ref_id=str(self.payment.id)).exists())

    def test_waive_and_escalate_do_not_settle_the_payment(self):
        for resolution in (DiscrepancyResolution.WAIVED, DiscrepancyResolution.ESCALATED):
            payment = self._payment(f'EXT-{resolution}', Decimal('100000.00'))
            self._resolve(self._discrepancy(payment=payment), resolution=resolution)
            payment.refresh_from_db()
            self.assertEqual(payment.status, PaymentStatus.PENDING, resolution)
        self.assertFalse(PaymentAllocation.objects.exists())

    def test_a_failed_settlement_leaves_the_discrepancy_pending(self):
        discrepancy = self._discrepancy()
        with mock.patch(
            'apps.finance.services.payments._finalize_payment_settlement', side_effect=RuntimeError('ledger down'),
        ):
            with self.assertRaises(RuntimeError):
                self._resolve(discrepancy)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.resolution, DiscrepancyResolution.PENDING)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PENDING)

    def test_manual_settle_of_a_discrepancy_without_a_payment_is_refused(self):
        """MISSING_IN_SYSTEM: the gateway holds money EduCore has no Payment for. Nothing can
        be allocated or journalled, so 'settled' must not be recorded as if it had been."""
        discrepancy = self._discrepancy()
        PaymentDiscrepancy.all_tenants.filter(pk=discrepancy.pk).update(payment=None)
        with self.assertRaisesMessage(ValueError, 'tidak memiliki pembayaran terkait'):
            self._resolve(discrepancy)
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.resolution, DiscrepancyResolution.PENDING)  # untouched, still open
        self.assertIsNone(discrepancy.resolved_at)
        self.assertFalse(LedgerJournal.objects.filter(ref_type='PAYMENT').exists())
        self.assertFalse(AuditEvent.objects.filter(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(discrepancy.id)).exists())

    def test_waive_and_escalate_still_work_without_a_payment(self):
        for resolution in (DiscrepancyResolution.WAIVED, DiscrepancyResolution.ESCALATED):
            discrepancy = self._discrepancy()
            PaymentDiscrepancy.all_tenants.filter(pk=discrepancy.pk).update(payment=None)
            self.assertEqual(self._resolve(discrepancy, resolution).resolution, resolution)

    def test_api_returns_400_for_manual_settle_without_a_payment(self):
        from rest_framework.test import APIClient
        from apps.identity.models import RoleAssignment
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.user, role='finance_officer',
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        discrepancy = self._discrepancy()
        PaymentDiscrepancy.all_tenants.filter(pk=discrepancy.pk).update(payment=None)
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.post(
            f'/api/v1/finance/reconciliation/discrepancies/{discrepancy.id}/resolve/',
            {'resolution': 'MANUAL_SETTLED'}, format='json',
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('pembayaran terkait', res.json()['error'])


class AutoSettleTests(ReconFixture):
    """The reconciliation cron's matched-and-not-yet-settled branch."""

    SETTLED_AT = datetime.datetime(2026, 9, 14, 3, 0, tzinfo=datetime.timezone.utc)

    def _record(self, external_id='EXT-1', amount=Decimal('500000.00'), fee=Decimal('3000.00')):
        return {
            'external_id': external_id, 'amount': amount, 'fee': fee, 'net': amount - fee,
            'settled_at': self.SETTLED_AT, 'channel': 'BCA_VA', 'bank': 'BCA', 'raw': {},
        }

    def _run(self, records, dry_run=False):
        provider = mock.MagicMock()
        provider.fetch_settlement.return_value = records
        with mock.patch('apps.finance.services.reconciliation.get_payment_provider', return_value=provider):
            return reconcile_gateway_settlement('MOCK', datetime.date(2026, 9, 14), self.foundation.id, dry_run=dry_run)

    def test_matched_pending_payment_is_settled_through_the_full_path(self):
        result = self._run([self._record()])
        self.assertEqual((result['matched'], result['errors']), (1, 0))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SETTLED)
        self.assertEqual(self.payment.settled_at, self.SETTLED_AT)  # the gateway's own settlement time
        self.assertTrue(self.payment.receipt_number)
        self.assertEqual(PaymentAllocation.objects.get(payment=self.payment).amount, Decimal('500000.00'))
        journal = LedgerJournal.objects.get(ref_type='PAYMENT', ref_id=str(self.payment.id))
        totals = LedgerEntry.objects.filter(journal=journal).aggregate(debit=Sum('debit'), credit=Sum('credit'))
        self.assertEqual(totals['debit'], totals['credit'])
        event = AuditEvent.objects.get(action='finance.payment.settled', entity_id=str(self.payment.id))
        self.assertEqual(event.role, 'RECONCILIATION_AUTO')

    def test_sub_tolerance_amount_difference_still_balances(self):
        # within AMOUNT_TOLERANCE (1.00): matched, but gateway net + fee != the recorded amount
        record = self._record(amount=Decimal('500000.50'), fee=Decimal('3000.00'))
        self._run([record])
        journal = LedgerJournal.objects.get(ref_type='PAYMENT', ref_id=str(self.payment.id))
        totals = LedgerEntry.objects.filter(journal=journal).aggregate(debit=Sum('debit'), credit=Sum('credit'))
        self.assertEqual(totals['debit'], totals['credit'])

    def test_a_pending_discrepancy_for_the_record_is_cleared(self):
        discrepancy = self._discrepancy()
        self._run([self._record()])
        discrepancy.refresh_from_db()
        self.assertEqual(discrepancy.resolution, DiscrepancyResolution.AUTO_SETTLED)

    def test_dry_run_settles_nothing(self):
        self._run([self._record()], dry_run=True)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PENDING)
        self.assertFalse(PaymentAllocation.objects.exists())

    def test_already_settled_payment_is_left_alone(self):
        self.payment.status = PaymentStatus.SETTLED
        self.payment.save(update_fields=['status'])
        result = self._run([self._record()])
        self.assertEqual(result['matched'], 1)
        self.assertFalse(PaymentAllocation.objects.exists())

    def test_one_failing_record_does_not_abort_the_batch_and_is_retried_later(self):
        other = self._payment('EXT-2', Decimal('100000.00'))
        real_finalize = __import__(
            'apps.finance.services.payments', fromlist=['_finalize_payment_settlement'],
        )._finalize_payment_settlement

        def flaky(payment, **kwargs):
            if payment.external_id == 'EXT-1':
                raise RuntimeError('ledger down')
            return real_finalize(payment, **kwargs)

        with mock.patch('apps.finance.services.payments._finalize_payment_settlement', side_effect=flaky):
            result = self._run([self._record('EXT-1'), self._record('EXT-2', Decimal('100000.00'), Decimal('1000.00'))])

        self.assertEqual((result['matched'], result['errors']), (1, 1))
        self.payment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.PENDING)  # untouched, rolled back
        self.assertEqual(other.status, PaymentStatus.SETTLED)         # the rest of the batch still ran
        batch = GatewaySettlementBatch.all_tenants.get(foundation_id=self.foundation.id, provider='MOCK')
        self.assertEqual(batch.status, SettlementBatchStatus.PARTIAL)  # not COMPLETED -> retried next run
        self.assertIn('could not be settled', batch.error_message)

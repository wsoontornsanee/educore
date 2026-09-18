"""Manual settlement of a payment discrepancy must reach AR and the ledger.

resolve_discrepancy(MANUAL_SETTLED) used to flip Payment.status to SETTLED and
stop: the money never got a receipt, an invoice allocation or a ledger journal.
It now runs the same settlement path a gateway webhook does.
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
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.identity.models import Foundation, Person, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ManualSettleTests(TestCase):
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

    def test_discrepancy_without_a_linked_payment_only_records_the_resolution(self):
        discrepancy = self._discrepancy()
        PaymentDiscrepancy.all_tenants.filter(pk=discrepancy.pk).update(payment=None)
        result = self._resolve(discrepancy)
        self.assertEqual(result.resolution, DiscrepancyResolution.MANUAL_SETTLED)
        self.assertFalse(LedgerJournal.objects.filter(ref_type='PAYMENT').exists())

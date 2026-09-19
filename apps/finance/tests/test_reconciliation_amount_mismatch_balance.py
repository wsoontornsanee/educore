"""AMOUNT_MISMATCH accounting treatment (spec/06 FIN-024): the gateway/system
delta reconciles into the student's (signed) StudentCreditBalance instead of
being silently discarded. Cash/Bank books the gateway's actual net; AR still
allocates at the billed (system) amount. A positive resulting balance sweeps
the student's other open invoices; a negative one raises an alert.
"""
import datetime
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import (
    DiscrepancyResolution, DiscrepancyType, GatewaySettlementBatch, Invoice, InvoiceStatus, LedgerEntry,
    LedgerJournal, Payment, PaymentDiscrepancy, PaymentMethod, PaymentStatus, SettlementBatchStatus,
    StudentCreditBalance,
)
from apps.finance.services.payments import apply_student_credit_to_invoices
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.notifications.models import ChannelType, NotificationIntent
from apps.notifications.services import render_template_message
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class _Base(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(legal_name='Yayasan Rekon 2', brand_name='Rekon2', npwp='01.777.666.5-444.001')
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD Rekon 2', npsn='20100078', level=School.LEVEL_SD, base_currency='IDR',
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Budi', nik='3171010101010078')
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person, nis='78', nisn='0078777777',
            status=Student.STATUS_ACTIVE,
        )
        parent_user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000078', email='guardian78@test.id', full_name='Orang Tua',
        )
        guardian_person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Orang Tua', nik='3171010101010079')
        self.guardian = Guardian.all_tenants.create(foundation_id=self.foundation.id, person=guardian_person, user=parent_user)
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student,
            relation=GuardianLink.RELATION_FATHER, is_primary=True, financial_responsible=True,
        )
        self.user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000079', email='rekon2@test.id', full_name='Bendahara',
        )
        self.invoice = self._invoice('INV/20100078/2026/000001', '2026-10', Decimal('500000.00'), issue_date='2026-10-01')
        self.batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='MOCK', settlement_date=datetime.date(2026, 9, 14),
            status=SettlementBatchStatus.PARTIAL,
        )
        self.payment = self._payment('EXT-1', Decimal('500000.00'))

    def tearDown(self):
        clear_current_foundation_id()

    def _invoice(self, number, period, total, issue_date='2026-10-01'):
        return Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number=number, period=period, issue_date=issue_date, due_date='2026-10-10',
            subtotal=total, total=total, currency='IDR', status=InvoiceStatus.ISSUED,
        )

    def _payment(self, external_id, amount, status=PaymentStatus.PENDING):
        return Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student, amount=amount,
            fee=Decimal('0.00'), net=amount, currency='IDR', method=PaymentMethod.VA, channel='BCA_VA',
            reference=f'PAY/20100078/2026/{external_id}', external_id=external_id, status=status,
            paid_at=timezone.now(),
        )

    def _discrepancy(self, payment, gateway_amount, gateway_fee=Decimal('0.00')):
        discrepancy = PaymentDiscrepancy(
            foundation_id=self.foundation.id, batch=self.batch, payment=payment, external_id=payment.external_id,
            discrepancy_type=DiscrepancyType.AMOUNT_MISMATCH, gateway_amount=gateway_amount, gateway_fee=gateway_fee,
            gateway_net=gateway_amount - gateway_fee, system_amount=payment.amount,
            resolution=DiscrepancyResolution.PENDING,
        )
        discrepancy.save()
        return discrepancy

    def _resolve(self, discrepancy):
        return resolve_discrepancy(
            discrepancy_id=discrepancy.id, resolution=DiscrepancyResolution.MANUAL_SETTLED, resolved_by=self.user,
            foundation_id=self.foundation.id, notes='Cocok dengan mutasi bank',
        )

    def _journal_totals(self, ref_type, ref_id):
        journal = LedgerJournal.objects.get(ref_type=ref_type, ref_id=str(ref_id))
        totals = LedgerEntry.objects.filter(journal=journal).aggregate(debit=Sum('debit'), credit=Sum('credit'))
        return totals['debit'], totals['credit']

    def _credit(self):
        return StudentCreditBalance.objects.get(student=self.student, currency='IDR')


class OverpaidGatewayDeltaTests(_Base):
    def test_cash_books_at_gateway_net_ar_at_system_amount_journal_balances(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('520000.00'), gateway_fee=Decimal('3000.00'))
        self._resolve(discrepancy)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.net, Decimal('517000.00'))  # gateway_amount - gateway_fee
        self.assertEqual(self.payment.fee, Decimal('3000.00'))
        debit, credit = self._journal_totals('PAYMENT', self.payment.id)
        self.assertEqual(debit, credit)
        self.assertEqual(debit, Decimal('520000.00'))  # net + fee

    def test_delta_lands_on_student_credit_balance_when_no_other_open_invoice(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('520000.00'))
        self._resolve(discrepancy)
        self.assertEqual(self._credit().balance, Decimal('20000.00'))

    def test_positive_balance_auto_sweeps_another_open_invoice_remainder_stays_credit(self):
        second_invoice = self._invoice('INV/20100078/2026/000002', '2026-11', Decimal('15000.00'), issue_date='2026-11-01')
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('520000.00'))  # +20000 delta
        self._resolve(discrepancy)

        second_invoice.refresh_from_db()
        self.assertEqual(second_invoice.status, InvoiceStatus.PAID)
        self.assertEqual(second_invoice.paid, Decimal('15000.00'))
        self.assertEqual(self._credit().balance, Decimal('5000.00'))  # 20000 - 15000 remainder

        debit, credit = self._journal_totals('CREDIT_APPLICATION', self._credit().id)
        self.assertEqual(debit, credit)
        self.assertEqual(debit, Decimal('15000.00'))

    def test_no_alert_dispatched_on_overpay(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('520000.00'))
        self._resolve(discrepancy)
        self.assertFalse(NotificationIntent.objects.filter(template_key='finance.reconcile_balance_negative').exists())


class UnderpaidGatewayDeltaTests(_Base):
    def test_cash_books_at_gateway_net_ar_still_at_system_amount_journal_balances(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'), gateway_fee=Decimal('3000.00'))
        self._resolve(discrepancy)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.net, Decimal('477000.00'))
        debit, credit = self._journal_totals('PAYMENT', self.payment.id)
        self.assertEqual(debit, credit)
        # Cash(477000) + Fee(3000) + Dr StudentCredit(20000 shortfall) == AR(500000, billed amount)
        self.assertEqual(debit, Decimal('500000.00'))

    def test_invoice_still_settles_at_the_full_billed_amount(self):
        """The shortfall is NOT taken out of what the student was billed —
        the invoice settles exactly as billed; only the reconcile balance
        reflects the gap."""
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'))
        self._resolve(discrepancy)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('500000.00'))
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)

    def test_shortfall_makes_the_balance_negative(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'))
        self._resolve(discrepancy)
        self.assertEqual(self._credit().balance, Decimal('-20000.00'))

    def test_negative_balance_dispatches_a_one_time_alert_to_financial_guardian(self):
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'))
        self._resolve(discrepancy)
        intent = NotificationIntent.objects.get(template_key='finance.reconcile_balance_negative')
        self.assertEqual(intent.recipient_user_id, self.guardian.user_id)
        self.assertEqual(intent.payload['amount'], 'Rp 20.000')

    def test_alert_renders_on_every_seeded_channel_from_the_dispatched_payload(self):
        """The dispatched payload must fill every placeholder of the seeded copy — no raw braces, the
        formatted shortfall present, and the guardian/student named where the channel addresses them."""
        from django.core.management import call_command
        call_command('seed_notification_templates', foundation_id=self.foundation.id, verbosity=0)
        self._resolve(self._discrepancy(self.payment, gateway_amount=Decimal('480000.00')))
        intent = NotificationIntent.objects.get(template_key='finance.reconcile_balance_negative')

        for channel in (ChannelType.WHATSAPP, ChannelType.PUSH, ChannelType.SMS):
            with self.subTest(channel=channel):
                rendered = render_template_message(
                    intent.template_key, channel, self.foundation.id, intent.payload,
                )
                text = f"{rendered['subject']} {rendered['body']}"
                self.assertNotIn('{', text)
                self.assertIn('Rp 20.000', rendered['body'])
                self.assertIn(intent.payload['student_name'], text)

    def test_alert_is_not_duplicated_on_a_second_shortfall_same_day(self):
        d1 = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'))
        self._resolve(d1)

        second_invoice = self._invoice('INV/20100078/2026/000002', '2026-11', Decimal('100000.00'), issue_date='2026-11-01')
        payment2 = self._payment('EXT-2', Decimal('100000.00'))
        d2 = self._discrepancy(payment2, gateway_amount=Decimal('90000.00'))
        self._resolve(d2)

        self.assertEqual(
            NotificationIntent.objects.filter(template_key='finance.reconcile_balance_negative').count(), 1,
        )
        # Same dedupe key -> the second call is deduplicated, so the balance
        # itself still reflects both shortfalls even though only one alert fired.
        self.assertEqual(self._credit().balance, Decimal('-30000.00'))

    def test_no_invoice_sweep_attempted_on_negative_balance(self):
        second_invoice = self._invoice('INV/20100078/2026/000002', '2026-11', Decimal('15000.00'), issue_date='2026-11-01')
        discrepancy = self._discrepancy(self.payment, gateway_amount=Decimal('480000.00'))
        self._resolve(discrepancy)
        second_invoice.refresh_from_db()
        self.assertEqual(second_invoice.status, InvoiceStatus.ISSUED)
        self.assertEqual(second_invoice.paid, Decimal('0.00'))


class ApplyStudentCreditToInvoicesTests(_Base):
    def test_noop_when_no_credit_balance_exists(self):
        touched, applied = apply_student_credit_to_invoices(self.student, self.foundation.id)
        self.assertEqual(touched, [])
        self.assertEqual(applied, Decimal('0.00'))

    def test_noop_when_balance_is_negative(self):
        StudentCreditBalance.objects.create(
            foundation_id=self.foundation.id, student=self.student, currency='IDR', balance=Decimal('-5000.00'),
        )
        touched, applied = apply_student_credit_to_invoices(self.student, self.foundation.id)
        self.assertEqual(touched, [])
        self.assertEqual(applied, Decimal('0.00'))

    def test_sweeps_oldest_invoice_first_and_stops_when_credit_exhausted(self):
        StudentCreditBalance.objects.create(
            foundation_id=self.foundation.id, student=self.student, currency='IDR', balance=Decimal('600000.00'),
        )
        older = self._invoice('INV/20100078/2026/000010', '2026-08', Decimal('200000.00'), issue_date='2026-08-01')
        newer = self._invoice('INV/20100078/2026/000011', '2026-09', Decimal('200000.00'), issue_date='2026-09-01')
        touched, applied = apply_student_credit_to_invoices(self.student, self.foundation.id)

        self.assertEqual(applied, Decimal('600000.00'))
        self.invoice.refresh_from_db()
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, InvoiceStatus.PAID)
        self.assertEqual(newer.status, InvoiceStatus.PAID)
        # self.invoice (2026-10, 500000) is the newest of the three (2026-08/09/10) and
        # only 200000 of credit remains after the two older ones (200000 each) -> partial.
        self.assertEqual(self.invoice.status, InvoiceStatus.PARTIALLY_PAID)
        self.assertEqual(self.invoice.paid, Decimal('200000.00'))
        self.assertEqual(self._credit().balance, Decimal('0.00'))

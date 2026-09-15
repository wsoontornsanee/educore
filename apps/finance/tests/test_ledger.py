from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import (
    AccountCode,
    Invoice,
    InvoiceStatus,
    LedgerEntry,
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
)
from apps.finance.services.ledger import (
    CurrencyMismatchError,
    UnbalancedLedgerError,
    post_invoice_issuance_journal,
    post_ledger_journal,
    post_payment_settlement_journal,
    post_revenue_recognition_journal,
)
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class LedgerServiceTests(TestCase):
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

        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Ahmad Faiz",
            nik="3201010101010001",
            gender=Person.GENDER_MALE,
            dob="2015-05-10",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="202601001",
            nisn="0012345678",
            status=Student.STATUS_ACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_post_journal_balance_assertion_passes_when_balanced(self):
        """CUR-020, FIN-021: Balanced journal entries post cleanly."""
        entries = [
            {
                'account_code': AccountCode.CASH_BANK,
                'account_name': 'Kas Bank',
                'debit': Decimal('500000.00'),
                'credit': Decimal('0.00'),
            },
            {
                'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
                'account_name': 'Piutang Siswa',
                'debit': Decimal('0.00'),
                'credit': Decimal('500000.00'),
            },
        ]
        journal = post_ledger_journal(
            school=self.school,
            ref_type='TEST',
            ref_id='1001',
            description='Test balanced journal',
            entries=entries,
            currency='IDR',
        )
        self.assertIsNotNone(journal.id)
        self.assertTrue(journal.number.startswith(f"JRN/{self.school.npsn}/"))
        self.assertEqual(journal.entries.count(), 2)

        total_debit = sum(e.debit for e in journal.entries.all())
        total_credit = sum(e.credit for e in journal.entries.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal('500000.00'))

    def test_post_journal_balance_assertion_fails_when_unbalanced(self):
        """CUR-020, FIN-021: Unbalanced journal raises UnbalancedLedgerError."""
        entries = [
            {
                'account_code': AccountCode.CASH_BANK,
                'account_name': 'Kas Bank',
                'debit': Decimal('500000.00'),
                'credit': Decimal('0.00'),
            },
            {
                'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
                'account_name': 'Piutang Siswa',
                'debit': Decimal('0.00'),
                'credit': Decimal('450000.00'),  # 50,000 difference!
            },
        ]
        with self.assertRaises(UnbalancedLedgerError):
            post_ledger_journal(
                school=self.school,
                ref_type='TEST',
                ref_id='1002',
                description='Test unbalanced journal',
                entries=entries,
                currency='IDR',
            )

    def test_post_journal_rejects_negative_amounts(self):
        """Debits and credits must be non-negative."""
        entries = [
            {
                'account_code': AccountCode.CASH_BANK,
                'account_name': 'Kas Bank',
                'debit': Decimal('-500000.00'),
                'credit': Decimal('0.00'),
            },
            {
                'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
                'account_name': 'Piutang Siswa',
                'debit': Decimal('0.00'),
                'credit': Decimal('-500000.00'),
            },
        ]
        with self.assertRaises(UnbalancedLedgerError):
            post_ledger_journal(
                school=self.school,
                ref_type='TEST',
                ref_id='1003',
                description='Negative amounts journal',
                entries=entries,
                currency='IDR',
            )

    def test_post_journal_rejects_mixed_currencies(self):
        """CUR-009: Mixing currencies in journal raises CurrencyMismatchError."""
        entries = [
            {
                'account_code': AccountCode.CASH_BANK,
                'account_name': 'Kas Bank',
                'debit': Decimal('500.00'),
                'credit': Decimal('0.00'),
                'currency': 'USD',
            },
            {
                'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
                'account_name': 'Piutang Siswa',
                'debit': Decimal('0.00'),
                'credit': Decimal('500.00'),
                'currency': 'IDR',
            },
        ]
        with self.assertRaises(CurrencyMismatchError):
            post_ledger_journal(
                school=self.school,
                ref_type='TEST',
                ref_id='1004',
                description='Mixed currency journal',
                entries=entries,
                currency='IDR',
            )

    def test_post_invoice_issuance_journal_balanced(self):
        """FIN-023: Invoice issuance writes balanced Dr AR / Cr Unearned + Rounding."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000001",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750050.00'),
            discount=Decimal('0.00'),
            rounding=Decimal('-50.00'),  # PEMBULATAN
            total=Decimal('750000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        journal = post_invoice_issuance_journal(invoice)
        self.assertIsNotNone(journal)
        self.assertEqual(journal.currency, 'IDR')

        total_debit = sum(e.debit for e in journal.entries.all())
        total_credit = sum(e.credit for e in journal.entries.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal('750050.00'))

    def test_post_payment_settlement_journal_balanced(self):
        """FIN-023: Payment settlement writes balanced Dr Cash, Dr Fee / Cr AR, Cr Overpayment."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000002",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('800000.00'),
            fee=Decimal('5000.00'),
            net=Decimal('795000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference=f"PAY/{self.school.npsn}/2026/000001",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )
        allocation = PaymentAllocation.objects.create(
            foundation_id=self.foundation.id,
            payment=payment,
            invoice=invoice,
            amount=Decimal('750000.00'),
            currency='IDR',
        )
        overpayment = Decimal('50000.00')

        journal = post_payment_settlement_journal(
            payment=payment,
            allocations=[allocation],
            overpayment=overpayment,
        )
        total_debit = sum(e.debit for e in journal.entries.all())
        total_credit = sum(e.credit for e in journal.entries.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal('800000.00'))

    def test_post_revenue_recognition_journal_balanced(self):
        """FIN-023: Revenue recognition writes balanced Dr Unearned / Cr Revenue."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000003",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            discount=Decimal('50000.00'),
            total=Decimal('700000.00'),
            currency='IDR',
            status=InvoiceStatus.PAID,
        )
        journal = post_revenue_recognition_journal(invoice)
        total_debit = sum(e.debit for e in journal.entries.all())
        total_credit = sum(e.credit for e in journal.entries.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal('700000.00'))

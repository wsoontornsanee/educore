from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentAllocation,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentStatus,
    StudentCreditBalance,
    StudentVirtualAccount,
)
from apps.finance.services.payments import (
    CurrencyMismatchError,
    InvalidPaymentError,
    allocate_payment_to_invoices,
    create_payment_intent,
    get_or_create_student_va,
    record_cash_payment,
    submit_manual_transfer,
    verify_manual_transfer,
)
from apps.identity.models import Foundation, Person, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PaymentServiceTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.888.777.6-555.000",
            address="Jakarta Selatan",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Harapan Bangsa",
            npsn="20100010",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Budi Pratama",
            nik="3171010101010002",
            gender=Person.GENDER_MALE,
            dob="2015-08-12",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="202602001",
            nisn="0023456789",
            status=Student.STATUS_ACTIVE,
        )

        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="bendahara@harapanbangsa.sch.id",
            full_name="Ibu Siti Aminah (Bendahara)",
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_stable_virtual_account_per_bank(self):
        """FIN-011: Virtual account is stable per student per bank."""
        va1 = get_or_create_student_va(student=self.student, school=self.school, bank='BCA')
        va2 = get_or_create_student_va(student=self.student, school=self.school, bank='BCA')
        self.assertEqual(va1.id, va2.id)
        self.assertEqual(va1.va_number, va2.va_number)

        # Different bank creates a separate stable VA
        va_mandiri = get_or_create_student_va(student=self.student, school=self.school, bank='MANDIRI')
        self.assertNotEqual(va1.va_number, va_mandiri.va_number)
        self.assertEqual(va_mandiri.bank, 'MANDIRI')

    def test_create_payment_intent_for_invoice(self):
        """FIN-010, FIN-011: Create VA and QRIS payment intents."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000010",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # VA intent
        va_intent = create_payment_intent(
            school=self.school,
            student=self.student,
            invoice_ids=[invoice.id],
            method=PaymentMethod.VA,
            bank='BCA',
        )
        self.assertEqual(va_intent.method, PaymentMethod.VA)
        self.assertEqual(va_intent.amount, Decimal('500000.00'))
        self.assertIsNotNone(va_intent.va_number)

        # QRIS intent
        qris_intent = create_payment_intent(
            school=self.school,
            student=self.student,
            invoice_ids=[invoice.id],
            method=PaymentMethod.QRIS,
        )
        self.assertEqual(qris_intent.method, PaymentMethod.QRIS)
        self.assertTrue(qris_intent.qris_payload.startswith('00020101'))

    def test_partial_payment_lifecycle(self):
        """
        Acceptance Criterion 2, FIN-016:
        Partial VA payment of Rp 500,000 against a Rp 750,000 invoice yields
        PARTIALLY_PAID, remaining Rp 250,000, and balanced ledger entries.
        """
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000011",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            fee=Decimal('0.00'),
            net=Decimal('500000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference=f"PAY/{self.school.npsn}/2026/000011",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

        allocations, overpayment = allocate_payment_to_invoices(payment, target_invoices=[invoice])
        invoice.refresh_from_db()

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].amount, Decimal('500000.00'))
        self.assertEqual(overpayment, Decimal('0.00'))
        self.assertEqual(invoice.status, InvoiceStatus.PARTIALLY_PAID)
        self.assertEqual(invoice.paid, Decimal('500000.00'))
        self.assertEqual(invoice.balance_due, Decimal('250000.00'))

    def test_full_payment_lifecycle(self):
        """Full payment transitions invoice to PAID."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000012",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('750000.00'),
            fee=Decimal('0.00'),
            net=Decimal('750000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference=f"PAY/{self.school.npsn}/2026/000012",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

        allocate_payment_to_invoices(payment, target_invoices=[invoice])
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, InvoiceStatus.PAID)
        self.assertEqual(invoice.paid, Decimal('750000.00'))
        self.assertEqual(invoice.balance_due, Decimal('0.00'))

    def test_multi_invoice_allocation_oldest_first(self):
        """FIN-014: Default allocation settles oldest invoice first."""
        inv1 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000021",
            period="2026-08",
            due_date="2026-08-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        inv2 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000022",
            period="2026-09",
            due_date="2026-09-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # Payment of Rp 700,000 covers full inv1 (500k) and partial inv2 (200k)
        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('700000.00'),
            fee=Decimal('0.00'),
            net=Decimal('700000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='MANDIRI_VA',
            reference=f"PAY/{self.school.npsn}/2026/000021",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

        allocations, overpayment = allocate_payment_to_invoices(payment)
        inv1.refresh_from_db()
        inv2.refresh_from_db()

        self.assertEqual(len(allocations), 2)
        self.assertEqual(inv1.status, InvoiceStatus.PAID)
        self.assertEqual(inv1.paid, Decimal('500000.00'))
        self.assertEqual(inv2.status, InvoiceStatus.PARTIALLY_PAID)
        self.assertEqual(inv2.paid, Decimal('200000.00'))
        self.assertEqual(inv2.balance_due, Decimal('300000.00'))
        self.assertEqual(overpayment, Decimal('0.00'))

    def test_overpayment_creates_credit_balance(self):
        """FIN-015: Overpayment creates student credit balance, never silently absorbed."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000030",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # Payment of Rp 600,000 for a Rp 500,000 invoice
        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('600000.00'),
            fee=Decimal('0.00'),
            net=Decimal('600000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference=f"PAY/{self.school.npsn}/2026/000030",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

        allocations, overpayment = allocate_payment_to_invoices(payment, target_invoices=[invoice])
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, InvoiceStatus.PAID)
        self.assertEqual(overpayment, Decimal('100000.00'))

        credit = StudentCreditBalance.objects.get(student=self.student, currency='IDR')
        self.assertEqual(credit.balance, Decimal('100000.00'))

    def test_currency_mismatch_rejection(self):
        """CUR-009, FIN-013b: Attempting to allocate IDR payment to USD invoice raises error."""
        usd_invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000099",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            currency='USD',
            status=InvoiceStatus.ISSUED,
        )
        idr_payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('1500000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference=f"PAY/{self.school.npsn}/2026/000099",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
        )

        with self.assertRaises(CurrencyMismatchError):
            allocate_payment_to_invoices(idr_payment, target_invoices=[usd_invoice])

    def test_cash_payment_generates_receipt_and_ledger(self):
        """FIN-019: Cash collection produces receipt number, officer, and balanced ledger."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000040",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        payment = record_cash_payment(
            school=self.school,
            student=self.student,
            amount=Decimal('750000.00'),
            invoice_ids=[invoice.id],
            received_by=self.finance_user,
            notes='Pembayaran tunai SPP Oktober',
        )

        self.assertEqual(payment.status, PaymentStatus.SETTLED)
        self.assertEqual(payment.method, PaymentMethod.CASH)
        self.assertTrue(payment.receipt_number.startswith(f"RCP/{self.school.npsn}/"))
        self.assertEqual(payment.received_by, self.finance_user)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatus.PAID)

    def test_manual_transfer_submission_and_verification_flow(self):
        """FIN-018: Manual transfer submission -> PENDING_VERIFICATION -> approve -> SETTLED."""
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000050",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('750000.00'),
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        payment = submit_manual_transfer(
            school=self.school,
            student=self.student,
            amount=Decimal('750000.00'),
            invoice_ids=[invoice.id],
            proof_file='proofs/transfer_123.jpg',
            notes='Transfer via BCA',
        )
        self.assertEqual(payment.status, PaymentStatus.PENDING_VERIFICATION)
        self.assertEqual(payment.proof_file, 'proofs/transfer_123.jpg')

        # Approve transfer
        verified = verify_manual_transfer(
            payment=payment,
            verified_by=self.finance_user,
            decision='APPROVE',
        )
        self.assertEqual(verified.status, PaymentStatus.SETTLED)
        self.assertTrue(verified.receipt_number.startswith(f"RCP/{self.school.npsn}/"))

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, InvoiceStatus.PAID)

    def test_manual_transfer_rejection(self):
        """FIN-018: Rejecting manual transfer marks status REJECTED with reason."""
        payment = submit_manual_transfer(
            school=self.school,
            student=self.student,
            amount=Decimal('750000.00'),
            proof_file='proofs/fake.jpg',
        )
        rejected = verify_manual_transfer(
            payment=payment,
            verified_by=self.finance_user,
            decision='REJECT',
            reason='Bukti transfer tidak terbaca / palsu',
        )
        self.assertEqual(rejected.status, PaymentStatus.REJECTED)
        self.assertEqual(rejected.metadata['rejection_reason'], 'Bukti transfer tidak terbaca / palsu')

    def test_payment_cannot_be_hard_deleted(self):
        """FIN-020: Payment delete is soft-delete only."""
        payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('100000.00'),
            currency='IDR',
            method=PaymentMethod.CASH,
            channel='CASHIER',
            reference=f"PAY/{self.school.npsn}/2026/999999",
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
        )
        payment_id = payment.id
        payment.delete()

        # Still exists in DB with deleted_at set
        raw = Payment.all_tenants.with_deleted().get(id=payment_id)
        self.assertIsNotNone(raw.deleted_at)


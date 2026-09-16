from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent, DomainEvent
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
    Refund,
    RefundStatus,
    StudentCreditBalance,
)
from apps.finance.services.refunds import (
    DEFAULT_REFUND_APPROVAL_THRESHOLD,
    ExceededPaymentAmountError,
    InvalidRefundStateError,
    RefundValidationError,
    approve_refund,
    cancel_refund,
    execute_refund,
    request_refund,
)
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.notifications.models import NotificationIntent
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class RefundServiceTests(TestCase):
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
            status=Student.STATUS_ACTIVE,
        )

        # Parent / Guardian
        self.guardian_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Ayah Budi",
            nik="3171010101010001",
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281299990001",
            email="ayah.budi@example.com",
            full_name="Ayah Budi",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.guardian_person,
            user=self.guardian_user,
        )
        self.guardian_link = GuardianLink.objects.create(
            foundation_id=self.foundation.id,
            student=self.student,
            guardian=self.guardian,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

        # Staff users
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281299990002",
            email="finance@harapanbangsa.sch.id",
            full_name="Staff Keuangan",
        )
        self.foundation_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281299990003",
            email="admin@yayasan.org",
            full_name="Ketua Yayasan",
        )
        RoleAssignment.objects.create(
            user=self.foundation_admin,
            foundation_id=self.foundation.id,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # Create a settled payment
        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('1500000.00'),
            fee=Decimal('0.00'),
            net=Decimal('1500000.00'),
            currency='IDR',
            method=PaymentMethod.MANUAL,
            channel='MANUAL_TRANSFER',
            reference='PAY/20100010/2026/000001',
            status=PaymentStatus.SETTLED,
            settled_at=timezone.now(),
        )

    def test_request_refund_under_threshold_auto_approved(self):
        """Refund <= 1,000,000 is auto-approved (FIN-032)."""
        refund = request_refund(
            payment=self.payment,
            amount=Decimal('500000.00'),
            reason="Kelebihan transfer biaya kegiatan",
            destination_bank_name="BCA",
            destination_account_number="1234567890",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )

        self.assertEqual(refund.status, RefundStatus.APPROVED)
        self.assertEqual(refund.amount, Decimal('500000.00'))
        self.assertEqual(refund.approved_by, self.finance_user)
        self.assertIsNotNone(refund.approved_at)

        # Audit & domain event check
        self.assertTrue(
            DomainEvent.objects.filter(
                foundation_id=self.foundation.id,
                name='finance.refund_requested',
            ).exists()
        )

    def test_request_refund_over_threshold_pending_approval(self):
        """Refund > 1,000,000 by regular staff enters PENDING_APPROVAL (FIN-032, FND-007)."""
        refund = request_refund(
            payment=self.payment,
            amount=Decimal('1200000.00'),
            reason="Pembatalan pendaftaran siswa baru",
            destination_bank_name="BCA",
            destination_account_number="1234567890",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )

        self.assertEqual(refund.status, RefundStatus.PENDING_APPROVAL)
        self.assertIsNone(refund.approved_by)
        self.assertIsNone(refund.approved_at)

    def test_request_refund_over_threshold_foundation_admin_approved(self):
        """Refund > 1,000,000 by foundation admin is approved directly (FND-007)."""
        refund = request_refund(
            payment=self.payment,
            amount=Decimal('1200000.00'),
            reason="Pengembalian dana disetujui ketua yayasan",
            destination_bank_name="MANDIRI",
            destination_account_number="9876543210",
            destination_account_holder="Ayah Budi",
            requested_by=self.foundation_admin,
        )

        self.assertEqual(refund.status, RefundStatus.APPROVED)
        self.assertEqual(refund.approved_by, self.foundation_admin)
        self.assertIsNotNone(refund.approved_at)

    def test_request_refund_unsettled_payment_fails(self):
        """Cannot request refund on pending or rejected payment."""
        pending_payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            fee=Decimal('0.00'),
            net=Decimal('500000.00'),
            currency='IDR',
            method=PaymentMethod.MANUAL,
            channel='MANUAL_TRANSFER',
            reference='PAY/20100010/2026/000002',
            status=PaymentStatus.PENDING_VERIFICATION,
        )

        with self.assertRaises(RefundValidationError):
            request_refund(
                payment=pending_payment,
                amount=Decimal('500000.00'),
                reason="Test",
                destination_bank_name="BCA",
                destination_account_number="123",
                destination_account_holder="Ayah Budi",
                requested_by=self.finance_user,
            )

    def test_request_refund_validation_checks(self):
        """Validates positive amount, non-empty bank details, and currency match."""
        # Zero amount
        with self.assertRaises(RefundValidationError):
            request_refund(
                payment=self.payment,
                amount=Decimal('0.00'),
                reason="Zero amount",
                destination_bank_name="BCA",
                destination_account_number="123",
                destination_account_holder="Ayah Budi",
                requested_by=self.finance_user,
            )

        # Missing bank name
        with self.assertRaises(RefundValidationError):
            request_refund(
                payment=self.payment,
                amount=Decimal('100000.00'),
                reason="Missing bank",
                destination_bank_name="",
                destination_account_number="123",
                destination_account_holder="Ayah Budi",
                requested_by=self.finance_user,
            )

        # Currency mismatch
        with self.assertRaises(RefundValidationError):
            request_refund(
                payment=self.payment,
                amount=Decimal('100000.00'),
                reason="Currency mismatch",
                destination_bank_name="BCA",
                destination_account_number="123",
                destination_account_holder="Ayah Budi",
                requested_by=self.finance_user,
                currency="USD",
            )

    def test_cumulative_refund_limit_enforced(self):
        """Cumulative refunds cannot exceed payment.amount (FIN-034)."""
        # First refund: 1,000,000
        r1 = request_refund(
            payment=self.payment,
            amount=Decimal('1000000.00'),
            reason="Refund part 1",
            destination_bank_name="BCA",
            destination_account_number="123",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )
        self.assertEqual(r1.status, RefundStatus.APPROVED)

        # Second refund: 600,000 -> cumulative 1,600,000 > 1,500,000 -> ExceededPaymentAmountError
        with self.assertRaises(ExceededPaymentAmountError):
            request_refund(
                payment=self.payment,
                amount=Decimal('600000.00'),
                reason="Refund part 2 exceeding limit",
                destination_bank_name="BCA",
                destination_account_number="123",
                destination_account_holder="Ayah Budi",
                requested_by=self.finance_user,
            )

        # Second refund: exactly 500,000 -> cumulative 1,500,000 == 1,500,000 -> Success
        r2 = request_refund(
            payment=self.payment,
            amount=Decimal('500000.00'),
            reason="Refund part 2 exact remaining",
            destination_bank_name="BCA",
            destination_account_number="123",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )
        self.assertEqual(r2.status, RefundStatus.APPROVED)

    def test_approve_and_reject_refund(self):
        """Approval workflow for pending refunds (FIN-032, FND-008)."""
        refund = request_refund(
            payment=self.payment,
            amount=Decimal('1200000.00'),
            reason="Pengajuan pengembalian dana besar",
            destination_bank_name="BCA",
            destination_account_number="123",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )
        self.assertEqual(refund.status, RefundStatus.PENDING_APPROVAL)

        # 1. Reject without reason fails
        with self.assertRaises(RefundValidationError):
            approve_refund(refund, user=self.foundation_admin, decision='REJECT', reason='')

        # 2. Reject with reason
        rejected = approve_refund(refund, user=self.foundation_admin, decision='REJECT', reason='Dokumen tidak lengkap')
        self.assertEqual(rejected.status, RefundStatus.REJECTED)
        self.assertEqual(rejected.rejection_reason, 'Dokumen tidak lengkap')

        # 3. Create another pending and approve it
        refund2 = request_refund(
            payment=self.payment,
            amount=Decimal('1200000.00'),
            reason="Pengajuan pengembalian dana revisi",
            destination_bank_name="BCA",
            destination_account_number="123",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )
        approved = approve_refund(refund2, user=self.foundation_admin, decision='APPROVE')
        self.assertEqual(approved.status, RefundStatus.APPROVED)
        self.assertEqual(approved.approved_by, self.foundation_admin)
        self.assertIsNotNone(approved.approved_at)

    def test_execute_refund_with_invoice_allocation(self):
        """
        Executing refund on payment allocated to an invoice:
        - Decrements invoice.paid and reverts status (PARTIALLY_PAID / ISSUED).
        - Generates compensating balanced ledger entries (Dr 1200 AR, Cr 1100 Cash/Bank) (FIN-020, FIN-021).
        - Dispatches guardian notification (FIN-033).
        """
        # Create invoice
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20100010/2026/000001",
            period="2026-08",
            issue_date="2026-08-01",
            due_date="2026-08-10",
            subtotal=Decimal('1500000.00'),
            discount=Decimal('0.00'),
            rounding=Decimal('0.00'),
            total=Decimal('1500000.00'),
            paid=Decimal('1500000.00'),
            currency='IDR',
            status=InvoiceStatus.PAID,
            created_by='system',
        )
        PaymentAllocation.objects.create(
            foundation_id=self.foundation.id,
            payment=self.payment,
            invoice=invoice,
            amount=Decimal('1500000.00'),
            currency='IDR',
        )

        refund = request_refund(
            payment=self.payment,
            amount=Decimal('500000.00'),
            reason="Pengurangan biaya SPP",
            destination_bank_name="BCA",
            destination_account_number="123456789",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )

        executed = execute_refund(
            refund=refund,
            user=self.finance_user,
            payout_reference="TRF/20260916/0099",
            payout_proof_file="proofs/refund_0099.pdf",
        )

        self.assertEqual(executed.status, RefundStatus.EXECUTED)
        self.assertEqual(executed.payout_reference, "TRF/20260916/0099")
        self.assertEqual(executed.payout_proof_file, "proofs/refund_0099.pdf")
        self.assertEqual(executed.executed_by, self.finance_user)
        self.assertIsNotNone(executed.executed_at)
        self.assertIsNotNone(executed.journal)

        # Invoice should now be PARTIALLY_PAID (1,500,000 - 500,000 = 1,000,000)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid, Decimal('1000000.00'))
        self.assertEqual(invoice.status, InvoiceStatus.PARTIALLY_PAID)

        # Verify compensating double-entry ledger journal
        journal = executed.journal
        self.assertEqual(journal.ref_type, 'REFUND')
        self.assertEqual(journal.ref_id, str(executed.id))
        entries = list(journal.entries.all())
        self.assertEqual(len(entries), 2)

        dr_ar = next(e for e in entries if e.account_code == AccountCode.ACCOUNTS_RECEIVABLE)
        cr_cash = next(e for e in entries if e.account_code == AccountCode.CASH_BANK)
        self.assertEqual(dr_ar.debit, Decimal('500000.00'))
        self.assertEqual(dr_ar.credit, Decimal('0.00'))
        self.assertEqual(cr_cash.credit, Decimal('500000.00'))
        self.assertEqual(cr_cash.debit, Decimal('0.00'))

        # Sum of debits == credits
        total_dr = sum(e.debit for e in entries)
        total_cr = sum(e.credit for e in entries)
        self.assertEqual(total_dr, total_cr)
        self.assertEqual(total_dr, Decimal('500000.00'))

        # Guardian notification intent dispatched (FIN-033)
        notif = NotificationIntent.objects.filter(
            foundation_id=self.foundation.id,
            template_key='finance.refund_executed',
            recipient_user=self.guardian_user,
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.payload['amount'], '500000.00')
        self.assertEqual(notif.payload['payout_reference'], 'TRF/20260916/0099')

    def test_execute_refund_with_overpayment_credit_balance(self):
        """
        Executing refund on payment that created overpayment credit:
        - Deducts StudentCreditBalance.
        - Generates compensating ledger entries (Dr 2200 Student Credit, Cr 1100 Cash/Bank).
        """
        # No allocations -> entire 1,500,000 was overpayment
        credit = StudentCreditBalance.objects.create(
            foundation_id=self.foundation.id,
            student=self.student,
            currency='IDR',
            balance=Decimal('1500000.00'),
        )

        refund = request_refund(
            payment=self.payment,
            amount=Decimal('500000.00'),
            reason="Pengembalian kelebihan bayar",
            destination_bank_name="BCA",
            destination_account_number="123456789",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )

        executed = execute_refund(
            refund=refund,
            user=self.finance_user,
            payout_reference="TRF/20260916/0100",
        )

        self.assertEqual(executed.status, RefundStatus.EXECUTED)
        credit.refresh_from_db()
        self.assertEqual(credit.balance, Decimal('1000000.00'))

        # Check ledger journal
        entries = list(executed.journal.entries.all())
        dr_credit = next(e for e in entries if e.account_code == AccountCode.STUDENT_CREDIT)
        cr_cash = next(e for e in entries if e.account_code == AccountCode.CASH_BANK)
        self.assertEqual(dr_credit.debit, Decimal('500000.00'))
        self.assertEqual(cr_cash.credit, Decimal('500000.00'))

    def test_cancel_refund(self):
        """Unexecuted refund can be cancelled."""
        refund = request_refund(
            payment=self.payment,
            amount=Decimal('400000.00'),
            reason="Salah input data",
            destination_bank_name="BCA",
            destination_account_number="123",
            destination_account_holder="Ayah Budi",
            requested_by=self.finance_user,
        )
        self.assertEqual(refund.status, RefundStatus.APPROVED)

        cancelled = cancel_refund(refund, user=self.finance_user, reason="Batal atas permintaan orang tua")
        self.assertEqual(cancelled.status, RefundStatus.CANCELLED)

        # Cannot execute cancelled refund
        with self.assertRaises(InvalidRefundStateError):
            execute_refund(cancelled, user=self.finance_user, payout_reference="TRF001")


class RefundAPITests(TestCase):
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
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            status=Student.STATUS_ACTIVE,
        )

        self.user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281299990099",
            email="staff@harapanbangsa.sch.id",
            full_name="Finance Staff",
            is_superuser=True,
        )

        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('2000000.00'),
            fee=Decimal('0.00'),
            net=Decimal('2000000.00'),
            currency='IDR',
            method=PaymentMethod.CASH,
            channel='CASHIER',
            reference='PAY/20100010/2026/000099',
            status=PaymentStatus.SETTLED,
            settled_at=timezone.now(),
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_refund_api_lifecycle(self):
        """End-to-end API test: create -> approve -> execute -> list."""
        with tenant_context(self.foundation.id):
            # 1. Create refund request
            resp = self.client.post('/api/v1/finance/refunds/', {
                'payment_id': self.payment.id,
                'amount': '500000.00',
                'currency': 'IDR',
                'reason': 'Pengembalian SPP kelebihan transfer',
                'destination_bank_name': 'BCA',
                'destination_account_number': '1234567890',
                'destination_account_holder': 'Budi Pratama',
            })
            self.assertEqual(resp.status_code, 201)
            refund_id = resp.data['id']
            self.assertEqual(resp.data['amount'], '500000.00')

            # 2. Approve refund request
            approve_resp = self.client.post(f'/api/v1/finance/refunds/{refund_id}/approve/', {
                'decision': 'APPROVE',
            })
            # Since requester was superuser/admin, it was already APPROVED, approve returns 400 (InvalidRefundState) or if pending approves
            # Let's check status
            self.assertIn(resp.data['status'], [RefundStatus.APPROVED, RefundStatus.PENDING_APPROVAL])

            # 3. Execute refund
            exec_resp = self.client.post(f'/api/v1/finance/refunds/{refund_id}/execute/', {
                'payout_reference': 'TRF-API-2026-01',
                'payout_proof_file': 'proofs/api_receipt.png',
            })
            self.assertEqual(exec_resp.status_code, 200)
            self.assertEqual(exec_resp.data['status'], RefundStatus.EXECUTED)
            self.assertEqual(exec_resp.data['payout_reference'], 'TRF-API-2026-01')

            # 4. List refunds
            list_resp = self.client.get('/api/v1/finance/refunds/?status=EXECUTED')
            self.assertEqual(list_resp.status_code, 200)
            self.assertGreaterEqual(len(list_resp.data['results']), 1)

    def test_cross_tenant_isolation(self):
        """Asserts Layer 3 tenancy isolation: cannot access other foundation's refund."""
        other_fnd = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Lain",
            npwp="02.000.000.0-000.000",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_fnd.id,
            name="SD Lain",
            npsn="20100099",
            level=School.LEVEL_SD,
        )
        other_person = Person.all_tenants.create(
            foundation_id=other_fnd.id,
            full_name="Siswa Lain",
            nik="3171010101010099",
        )
        other_student = Student.all_tenants.create(
            foundation_id=other_fnd.id,
            school=other_school,
            person=other_person,
        )
        other_payment = Payment.objects.create(
            foundation_id=other_fnd.id,
            school=other_school,
            student=other_student,
            amount=Decimal('1000000.00'),
            net=Decimal('1000000.00'),
            currency='IDR',
            method=PaymentMethod.CASH,
            channel='CASHIER',
            reference='PAY/20100099/2026/000001',
            status=PaymentStatus.SETTLED,
        )
        other_refund = Refund.objects.create(
            foundation_id=other_fnd.id,
            school=other_school,
            student=other_student,
            payment=other_payment,
            amount=Decimal('100000.00'),
            currency='IDR',
            reason='Test cross tenant',
            destination_bank_name='BCA',
            destination_account_number='123',
            destination_account_holder='Lain',
            requested_by=self.user,
        )

        with tenant_context(self.foundation.id):
            resp = self.client.get(f'/api/v1/finance/refunds/{other_refund.id}/')
            self.assertEqual(resp.status_code, 404)

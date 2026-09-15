import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.finance.models import (
    FeeCategory,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceInstallment,
    InvoiceInstallmentStatus,
    InvoiceLine,
    InvoiceStatus,
)
from apps.finance.services import (
    InstallmentPlanAlreadyExistsError,
    InvalidInstallmentError,
    allocate_payment_to_installments,
    calculate_installments_largest_remainder,
    cancel_installment_plan,
    create_installment_plan,
    is_invoice_reminder_still_needed,
    record_cash_payment,
    run_arrears_ladder,
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
from apps.notifications.models import (
    ChannelType,
    IntentStatus,
    NotificationCategory,
    NotificationIntent,
)
from educore.middleware.tenancy import (
    clear_current_foundation_id,
    set_current_foundation_id,
    tenant_context,
)


class InstallmentPlanTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        # Foundation A
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Installment Test A",
            brand_name="Installment A",
            npwp="01.222.333.4-555.001",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation_a.id)

        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation_a.id,
            name="SD Installment Test A",
            npsn="10199991",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        self.user_a = User.objects.create(
            foundation_id=self.foundation_a.id,
            phone_e164="+6281111111111",
            email="finance_a@example.com",
            full_name="Staff Finance A",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation_a.id,
            user=self.user_a,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation_a.id,
        )

        # Student & Guardian in School A
        self.student_person_a = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Ananda Installment",
            dob="2015-05-10",
            gender="M",
        )
        self.student_a = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a,
            person=self.student_person_a,
            nisn="1234567890",
            status="ACTIVE",
        )

        self.guardian_person_a = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Bapak Installment",
            dob="1980-01-01",
            gender="M",
        )
        self.guardian_user_a = User.objects.create(
            foundation_id=self.foundation_a.id,
            phone_e164="+6281222222222",
            email="guardian_inst_a@example.com",
            full_name="Bapak Installment User",
        )
        self.guardian_a = Guardian.all_tenants.create(
            foundation_id=self.foundation_a.id,
            person=self.guardian_person_a,
            user=self.guardian_user_a,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation_a.id,
            student=self.student_a,
            guardian=self.guardian_a,
            relation="FATHER",
            is_primary=True,
            financial_responsible=True,
        )

        # FeeType and Invoice in School A
        self.fee_type = FeeType.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a,
            code="PANGKAL",
            name="Uang Pangkal",
            category=FeeCategory.ENTRY,
            recurrence=FeeRecurrence.ONE_OFF,
            default_amount=Decimal('1000000.00'),
            currency='IDR',
        )

        self.invoice = Invoice.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a,
            student=self.student_a,
            number="INV/SD01/2026/000001",
            period="2026-07",
            issue_date=datetime.date(2026, 7, 1),
            due_date=datetime.date(2026, 7, 10),
            subtotal=Decimal('1000000.00'),
            discount=Decimal('0.00'),
            rounding=Decimal('0.00'),
            total=Decimal('1000000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        InvoiceLine.all_tenants.create(
            foundation_id=self.foundation_a.id,
            invoice=self.invoice,
            fee_type=self.fee_type,
            code="PANGKAL",
            description="Uang Pangkal",
            amount=Decimal('1000000.00'),
            discount=Decimal('0.00'),
            subtotal=Decimal('1000000.00'),
            currency='IDR',
        )

        # Foundation B (for cross-tenant tests)
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Installment Test B",
            brand_name="Installment B",
            npwp="01.222.333.4-555.002",
            address="Jakarta",
        )
        with tenant_context(self.foundation_b.id):
            self.school_b = School.all_tenants.create(
                foundation_id=self.foundation_b.id,
                name="SD Installment Test B",
                npsn="10199992",
                level=School.LEVEL_SD,
                base_currency="IDR",
            )
            self.user_b = User.objects.create(
                foundation_id=self.foundation_b.id,
                phone_e164="+6281333333333",
                email="finance_b@example.com",
                full_name="Staff Finance B",
            )
            RoleAssignment.all_tenants.create(
                foundation_id=self.foundation_b.id,
                user=self.user_b,
                role=RoleAssignment.ROLE_FINANCE_OFFICER,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                scope_id=self.foundation_b.id,
            )

    def tear_down(self):
        clear_current_foundation_id()

    def test_largest_remainder_idr_cur_018_acceptance_criterion_3(self):
        """
        spec/16 §8 Acceptance criteria #3:
        Splitting 1000000.00 into 3 installments produces 333400.00 + 333300.00 + 333300.00
        and sums exactly to the total.
        """
        schedule = calculate_installments_largest_remainder(
            total_amount=Decimal('1000000.00'),
            count=3,
            first_due_date=datetime.date(2026, 7, 10),
            interval_days=30,
            currency='IDR',
        )
        self.assertEqual(len(schedule), 3)
        amounts = [item['amount'] for item in schedule]
        self.assertEqual(
            amounts,
            [Decimal('333400.00'), Decimal('333300.00'), Decimal('333300.00')]
        )
        self.assertEqual(sum(amounts), Decimal('1000000.00'))
        self.assertEqual(schedule[0]['due_date'], datetime.date(2026, 7, 10))
        self.assertEqual(schedule[1]['due_date'], datetime.date(2026, 8, 9))
        self.assertEqual(schedule[2]['due_date'], datetime.date(2026, 9, 8))

    def test_largest_remainder_usd(self):
        """
        Non-IDR largest-remainder distribution with 0.01 rounding unit.
        Splitting USD 100.00 into 3 installments -> 33.34 + 33.33 + 33.33 = 100.00.
        """
        schedule = calculate_installments_largest_remainder(
            total_amount=Decimal('100.00'),
            count=3,
            first_due_date=datetime.date(2026, 7, 10),
            interval_days=30,
            currency='USD',
        )
        self.assertEqual(len(schedule), 3)
        amounts = [item['amount'] for item in schedule]
        self.assertEqual(
            amounts,
            [Decimal('33.34'), Decimal('33.33'), Decimal('33.33')]
        )
        self.assertEqual(sum(amounts), Decimal('100.00'))

    def test_create_installment_plan_auto(self):
        """Auto-generate installments on an issued invoice."""
        installments = create_installment_plan(
            invoice=self.invoice,
            count=3,
            first_due_date=datetime.date(2026, 7, 15),
            interval_days=30,
            user=self.user_a,
        )
        self.assertEqual(len(installments), 3)
        self.assertEqual(self.invoice.installments.count(), 3)
        inst1, inst2, inst3 = installments

        self.assertEqual(inst1.installment_no, 1)
        self.assertEqual(inst1.amount, Decimal('333400.00'))
        self.assertEqual(inst1.paid_amount, Decimal('0.00'))
        self.assertEqual(inst1.balance_due, Decimal('333400.00'))
        self.assertEqual(inst1.status, InvoiceInstallmentStatus.PENDING)

        self.assertEqual(inst2.installment_no, 2)
        self.assertEqual(inst2.amount, Decimal('333300.00'))

        self.assertEqual(inst3.installment_no, 3)
        self.assertEqual(inst3.amount, Decimal('333300.00'))

    def test_create_installment_plan_custom_schedule(self):
        """Provide custom schedule matching balance due exactly."""
        custom_schedule = [
            {'due_date': '2026-07-20', 'amount': '500000.00'},
            {'due_date': '2026-08-20', 'amount': '300000.00'},
            {'due_date': '2026-09-20', 'amount': '200000.00'},
        ]
        installments = create_installment_plan(
            invoice=self.invoice,
            schedule=custom_schedule,
            user=self.user_a,
        )
        self.assertEqual(len(installments), 3)
        amounts = [inst.amount for inst in installments]
        self.assertEqual(amounts, [Decimal('500000.00'), Decimal('300000.00'), Decimal('200000.00')])
        self.assertEqual(sum(amounts), self.invoice.balance_due)

    def test_create_installment_plan_mismatched_sum_rejected(self):
        """Custom schedule whose sum does not equal balance_due is rejected."""
        custom_schedule = [
            {'due_date': '2026-07-20', 'amount': '500000.00'},
            {'due_date': '2026-08-20', 'amount': '400000.00'},  # sum is 900,000, invoice is 1,000,000
        ]
        with self.assertRaises(InvalidInstallmentError) as ctx:
            create_installment_plan(
                invoice=self.invoice,
                schedule=custom_schedule,
                user=self.user_a,
            )
        self.assertIn("Total rencana cicilan", str(ctx.exception))

    def test_create_installment_plan_already_exists_rejected(self):
        """Cannot create installment plan if active plan already exists."""
        create_installment_plan(invoice=self.invoice, count=2, user=self.user_a)
        with self.assertRaises(InstallmentPlanAlreadyExistsError):
            create_installment_plan(invoice=self.invoice, count=3, user=self.user_a)

    def test_create_installment_plan_invalid_invoice_status_rejected(self):
        """Cannot create installment plan on PAID or CANCELLED invoices."""
        self.invoice.status = InvoiceStatus.PAID
        self.invoice.save(update_fields=['status'])

        with self.assertRaises(InvalidInstallmentError):
            create_installment_plan(invoice=self.invoice, count=2, user=self.user_a)

    def test_payment_allocation_cascades_to_installments(self):
        """
        FIN-030: When payment is recorded, it cascades across active installments oldest-first.
        """
        create_installment_plan(
            invoice=self.invoice,
            count=3,
            first_due_date=datetime.date(2026, 7, 10),
            interval_days=30,
            user=self.user_a,
        )
        # Installments:
        # #1: 333,400.00
        # #2: 333,300.00
        # #3: 333,300.00

        # Pay Rp 400,000 in cash
        payment1 = record_cash_payment(
            school=self.school_a,
            student=self.student_a,
            amount=Decimal('400000.00'),
            invoice_ids=[self.invoice.id],
            received_by=self.user_a,
        )

        inst1 = self.invoice.installments.get(installment_no=1)
        inst2 = self.invoice.installments.get(installment_no=2)
        inst3 = self.invoice.installments.get(installment_no=3)

        # Inst 1 is fully paid
        self.assertEqual(inst1.status, InvoiceInstallmentStatus.PAID)
        self.assertEqual(inst1.paid_amount, Decimal('333400.00'))
        self.assertEqual(inst1.balance_due, Decimal('0.00'))
        self.assertIsNotNone(inst1.paid_at)

        # Inst 2 has 400,000 - 333,400 = 66,600 paid
        self.assertEqual(inst2.status, InvoiceInstallmentStatus.PARTIALLY_PAID)
        self.assertEqual(inst2.paid_amount, Decimal('66600.00'))
        self.assertEqual(inst2.balance_due, Decimal('266700.00'))

        # Inst 3 is untouched
        self.assertEqual(inst3.status, InvoiceInstallmentStatus.PENDING)
        self.assertEqual(inst3.paid_amount, Decimal('0.00'))

        # Pay remaining Rp 600,000
        payment2 = record_cash_payment(
            school=self.school_a,
            student=self.student_a,
            amount=Decimal('600000.00'),
            invoice_ids=[self.invoice.id],
            received_by=self.user_a,
        )

        inst2.refresh_from_db()
        inst3.refresh_from_db()
        self.invoice.refresh_from_db()

        self.assertEqual(inst2.status, InvoiceInstallmentStatus.PAID)
        self.assertEqual(inst2.paid_amount, Decimal('333300.00'))
        self.assertEqual(inst3.status, InvoiceInstallmentStatus.PAID)
        self.assertEqual(inst3.paid_amount, Decimal('333300.00'))
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)

    def test_cancel_installment_plan(self):
        """
        Cancelling an installment plan cancels unpaid installments while preserving paid history.
        """
        create_installment_plan(
            invoice=self.invoice,
            count=3,
            first_due_date=datetime.date(2026, 7, 10),
            interval_days=30,
            user=self.user_a,
        )

        # Pay first installment (333,400)
        record_cash_payment(
            school=self.school_a,
            student=self.student_a,
            amount=Decimal('333400.00'),
            invoice_ids=[self.invoice.id],
            received_by=self.user_a,
        )

        # Cancel the plan
        cancel_installment_plan(invoice=self.invoice, user=self.user_a, reason="Permintaan orang tua restrukturisasi")

        inst1 = self.invoice.installments.get(installment_no=1)
        inst2 = self.invoice.installments.get(installment_no=2)
        inst3 = self.invoice.installments.get(installment_no=3)

        self.assertEqual(inst1.status, InvoiceInstallmentStatus.PAID)
        self.assertEqual(inst2.status, InvoiceInstallmentStatus.CANCELLED)
        self.assertEqual(inst3.status, InvoiceInstallmentStatus.CANCELLED)
        self.assertIn("restrukturisasi", inst2.notes)

    def test_arrears_reminder_ladder_follows_installment_dates(self):
        """
        FIN-030: Reminders follow installment due dates, evaluating each active installment
        against ladder offsets rather than the overarching invoice due date.
        """
        create_installment_plan(
            invoice=self.invoice,
            count=3,
            first_due_date=datetime.date(2026, 7, 10),
            interval_days=30,
            user=self.user_a,
        )
        # Installment #1 due 2026-07-10 (T-0 on 2026-07-10)
        # Installment #2 due 2026-08-09
        # Installment #3 due 2026-09-08

        # Run arrears ladder as of 2026-07-10 (T-0 for Inst #1)
        report = run_arrears_ladder(school=self.school_a, as_of_date=datetime.date(2026, 7, 10))
        self.assertEqual(report['dispatched_count'], 1)
        self.assertEqual(len(report['reminders']), 1)
        reminder = report['reminders'][0]
        self.assertEqual(reminder['installment_no'], 1)
        self.assertEqual(reminder['step_label'], 'T-0')
        self.assertIn('inst1:0', reminder['dedupe_key'])

        intent = NotificationIntent.objects.get(dedupe_key__contains='inst1:0')
        self.assertEqual(intent.payload['installment_no'], 1)
        self.assertEqual(intent.payload['balance_due'], '333400.00')

        # Check send-time re-evaluator: still needed because installment is pending
        self.assertTrue(is_invoice_reminder_still_needed(intent))

        # Now pay installment #1
        record_cash_payment(
            school=self.school_a,
            student=self.student_a,
            amount=Decimal('333400.00'),
            invoice_ids=[self.invoice.id],
            received_by=self.user_a,
        )

        # Re-evaluator should now return False (cancelled per FIN-027, NTF-004)
        self.assertFalse(is_invoice_reminder_still_needed(intent))

        # Advance to 2026-08-09 (T-0 for Inst #2)
        report2 = run_arrears_ladder(school=self.school_a, as_of_date=datetime.date(2026, 8, 9))
        self.assertEqual(report2['dispatched_count'], 1)
        reminder2 = report2['reminders'][0]
        self.assertEqual(reminder2['installment_no'], 2)
        self.assertEqual(reminder2['step_label'], 'T-0')

    def test_api_installments_lifecycle_and_cross_tenant(self):
        """
        API testing for /api/v1/finance/invoices/:id/installments/
        - GET list of installments
        - POST create installment plan
        - DELETE cancel installment plan
        - Cross-tenant isolation asserts 404 for sibling foundation
        """
        self.client.force_authenticate(user=self.user_a)

        # 1. GET initially empty
        res = self.client.get(f"/api/v1/finance/invoices/{self.invoice.id}/installments/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

        # 2. POST create installment plan (3 installments)
        payload = {
            'count': 3,
            'first_due_date': '2026-07-15',
            'interval_days': 30,
            'notes': 'Cicilan 3x Uang Pangkal',
        }
        res_post = self.client.post(f"/api/v1/finance/invoices/{self.invoice.id}/installments/", payload, format='json')
        self.assertEqual(res_post.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res_post.data), 3)
        self.assertEqual(res_post.data[0]['amount'], '333400.00')
        self.assertEqual(res_post.data[1]['amount'], '333300.00')
        self.assertEqual(res_post.data[2]['amount'], '333300.00')

        # 3. GET on invoice detail exposes nested installments
        res_inv = self.client.get(f"/api/v1/finance/invoices/{self.invoice.id}/")
        self.assertEqual(res_inv.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_inv.data['installments']), 3)

        # 4. POST duplicate attempt returns 409 CONFLICT
        res_dup = self.client.post(f"/api/v1/finance/invoices/{self.invoice.id}/installments/", payload, format='json')
        self.assertEqual(res_dup.status_code, status.HTTP_409_CONFLICT)

        # 5. Cross-tenant test: User B (Foundation B) accessing Invoice A gets 404
        self.client.force_authenticate(user=self.user_b)
        set_current_foundation_id(self.foundation_b.id)

        res_cross_get = self.client.get(f"/api/v1/finance/invoices/{self.invoice.id}/installments/")
        self.assertEqual(res_cross_get.status_code, status.HTTP_404_NOT_FOUND)

        res_cross_post = self.client.post(f"/api/v1/finance/invoices/{self.invoice.id}/installments/", payload, format='json')
        self.assertEqual(res_cross_post.status_code, status.HTTP_404_NOT_FOUND)

        res_cross_del = self.client.delete(f"/api/v1/finance/invoices/{self.invoice.id}/installments/")
        self.assertEqual(res_cross_del.status_code, status.HTTP_404_NOT_FOUND)

        # 6. User A cancels installments via DELETE
        self.client.force_authenticate(user=self.user_a)
        set_current_foundation_id(self.foundation_a.id)

        res_del = self.client.delete(f"/api/v1/finance/invoices/{self.invoice.id}/installments/", {'reason': 'Dibatalkan oleh TU'}, format='json')
        self.assertEqual(res_del.status_code, status.HTTP_200_OK)
        self.assertEqual(res_del.data[0]['status'], InvoiceInstallmentStatus.CANCELLED)

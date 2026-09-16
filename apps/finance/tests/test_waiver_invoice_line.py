import datetime
from decimal import Decimal
from django.test import TestCase
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.finance.models import (
    AccountCode,
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    LedgerEntry,
)
from apps.finance.services import approve_discount, reject_discount
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class WaiverInvoiceLineTests(TestCase):
    """FND-007 acceptance criterion #2 (spec/03 §6.2): approving a waiver
    (FIXED-type discount) marks the underlying invoice line WAIVED and
    emits finance.waiver.approved."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Generasi Emas",
            brand_name="Generasi Emas",
            npwp="01.234.567.8-333.000",
            address="Semarang",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Generasi Emas",
            npsn="20700001",
            level=School.LEVEL_SD,
        )
        self.admin_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123445566",
            email="admin@emas.sch.id",
            full_name="Admin Yayasan",
        )
        RoleAssignment.all_tenants.create(
            user=self.admin_user,
            foundation_id=self.foundation.id,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Pertama",
            dob=datetime.date(2014, 3, 15),
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nisn="1000000001",
            nis="SD-01",
            status=Student.STATUS_ACTIVE,
        )
        self.fee_spp = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_SD",
            name="SPP SD",
            category=FeeCategory.SPP,
            default_amount=Decimal('1000000.00'),
        )
        with tenant_context(self.foundation.id):
            self.invoice = Invoice.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                student=self.student,
                number="INV/20700001/2026/000001",
                period="2026-10",
                issue_date=datetime.date(2026, 10, 1),
                due_date=datetime.date(2026, 10, 10),
                subtotal=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                rounding=Decimal('0.00'),
                total=Decimal('1000000.00'),
                paid=Decimal('0.00'),
                currency='IDR',
                status=InvoiceStatus.ISSUED,
            )
            self.line = InvoiceLine.objects.create(
                foundation_id=self.foundation.id,
                invoice=self.invoice,
                fee_type=self.fee_spp,
                code="SPP_SD",
                description="SPP SD",
                amount=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                subtotal=Decimal('1000000.00'),
                currency='IDR',
            )

    def _create_waiver(self, value):
        return Discount.objects.create(
            foundation_id=self.foundation.id,
            student=self.student,
            fee_type=self.fee_spp,
            type=DiscountType.FIXED,
            value=value,
            reason="Keringanan Khusus Yayasan",
            valid_from=datetime.date(2026, 7, 1),
            status=DiscountStatus.PENDING_APPROVAL,
        )

    def test_approving_waiver_marks_invoice_line_waived(self):
        with tenant_context(self.foundation.id):
            disc = self._create_waiver(Decimal('1000000.00'))
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            self.invoice.refresh_from_db()
            self.assertTrue(self.line.waived)
            self.assertEqual(self.line.discount, Decimal('1000000.00'))
            self.assertEqual(self.line.subtotal, Decimal('0.00'))
            self.assertEqual(self.invoice.discount, Decimal('1000000.00'))
            self.assertEqual(self.invoice.total, Decimal('0.00'))
            self.assertEqual(self.invoice.status, InvoiceStatus.PAID)

    def test_approving_partial_waiver_reduces_line_without_fully_waiving_balance(self):
        with tenant_context(self.foundation.id):
            disc = self._create_waiver(Decimal('400000.00'))
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            self.invoice.refresh_from_db()
            self.assertFalse(self.line.waived)
            self.assertEqual(self.line.discount, Decimal('400000.00'))
            self.assertEqual(self.line.subtotal, Decimal('600000.00'))
            self.assertEqual(self.invoice.total, Decimal('600000.00'))
            self.assertEqual(self.invoice.status, InvoiceStatus.ISSUED)

    def test_percent_discount_does_not_touch_invoice_line(self):
        with tenant_context(self.foundation.id):
            disc = Discount.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                fee_type=self.fee_spp,
                type=DiscountType.PERCENT,
                value=Decimal('50.00'),
                reason="Diskon Prestasi",
                valid_from=datetime.date(2026, 7, 1),
                status=DiscountStatus.PENDING_APPROVAL,
            )
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            self.invoice.refresh_from_db()
            self.assertFalse(self.line.waived)
            self.assertEqual(self.line.discount, Decimal('0.00'))
            self.assertEqual(self.invoice.total, Decimal('1000000.00'))

    def test_waiver_with_no_matching_invoice_line_is_a_no_op(self):
        with tenant_context(self.foundation.id):
            other_fee = FeeType.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                code="UANG_GEDUNG",
                name="Uang Gedung",
                category=FeeCategory.SPP,
                default_amount=Decimal('500000.00'),
            )
            disc = Discount.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                fee_type=other_fee,
                type=DiscountType.FIXED,
                value=Decimal('500000.00'),
                reason="Keringanan Khusus Yayasan",
                valid_from=datetime.date(2026, 7, 1),
                status=DiscountStatus.PENDING_APPROVAL,
            )
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            self.invoice.refresh_from_db()
            self.assertFalse(self.line.waived)
            self.assertEqual(self.invoice.total, Decimal('1000000.00'))

    def test_waiver_skips_fully_waived_line_and_targets_next_open_one(self):
        with tenant_context(self.foundation.id):
            self.line.waived = True
            self.line.discount = self.line.amount
            self.line.subtotal = Decimal('0.00')
            self.line.save(update_fields=['waived', 'discount', 'subtotal'])

            later_invoice = Invoice.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                student=self.student,
                number="INV/20700001/2026/000002",
                period="2026-11",
                issue_date=datetime.date(2026, 11, 1),
                due_date=datetime.date(2026, 11, 10),
                subtotal=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                rounding=Decimal('0.00'),
                total=Decimal('1000000.00'),
                paid=Decimal('0.00'),
                currency='IDR',
                status=InvoiceStatus.ISSUED,
            )
            later_line = InvoiceLine.objects.create(
                foundation_id=self.foundation.id,
                invoice=later_invoice,
                fee_type=self.fee_spp,
                code="SPP_SD",
                description="SPP SD",
                amount=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                subtotal=Decimal('1000000.00'),
                currency='IDR',
            )

            disc = self._create_waiver(Decimal('1000000.00'))
            approve_discount(disc, user=self.admin_user)

            later_line.refresh_from_db()
            self.assertTrue(later_line.waived)

    def test_approving_waiver_posts_discount_expense_ledger_journal(self):
        with tenant_context(self.foundation.id):
            disc = self._create_waiver(Decimal('1000000.00'))
            approve_discount(disc, user=self.admin_user)

            entries = LedgerEntry.objects.filter(ref_type='WAIVER', ref_id=str(self.invoice.id))
            self.assertEqual(entries.count(), 2)
            debit_entry = entries.get(account_code=AccountCode.DISCOUNT_EXPENSE)
            credit_entry = entries.get(account_code=AccountCode.ACCOUNTS_RECEIVABLE)
            self.assertEqual(debit_entry.debit, Decimal('1000000.00'))
            self.assertEqual(credit_entry.credit, Decimal('1000000.00'))

    def test_partial_waiver_does_not_mark_line_fully_waived_and_leaves_it_eligible(self):
        with tenant_context(self.foundation.id):
            disc1 = self._create_waiver(Decimal('300000.00'))
            approve_discount(disc1, user=self.admin_user)
            self.line.refresh_from_db()
            self.assertFalse(self.line.waived)
            self.assertEqual(self.line.subtotal, Decimal('700000.00'))

            # A second waiver on the same fee_type continues reducing the
            # same still-open line rather than being blocked by a stale
            # "already touched" flag.
            disc2 = self._create_waiver(Decimal('700000.00'))
            approve_discount(disc2, user=self.admin_user)
            self.line.refresh_from_db()
            self.assertTrue(self.line.waived)
            self.assertEqual(self.line.subtotal, Decimal('0.00'))
            self.assertEqual(self.line.discount, Decimal('1000000.00'))

    def test_waiver_targets_earliest_open_period_not_most_recent(self):
        with tenant_context(self.foundation.id):
            later_invoice = Invoice.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                student=self.student,
                number="INV/20700001/2026/000002",
                period="2026-11",
                issue_date=datetime.date(2026, 11, 1),
                due_date=datetime.date(2026, 11, 10),
                subtotal=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                rounding=Decimal('0.00'),
                total=Decimal('1000000.00'),
                paid=Decimal('0.00'),
                currency='IDR',
                status=InvoiceStatus.ISSUED,
            )
            InvoiceLine.objects.create(
                foundation_id=self.foundation.id,
                invoice=later_invoice,
                fee_type=self.fee_spp,
                code="SPP_SD",
                description="SPP SD",
                amount=Decimal('1000000.00'),
                discount=Decimal('0.00'),
                subtotal=Decimal('1000000.00'),
                currency='IDR',
            )

            disc = self._create_waiver(Decimal('1000000.00'))
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            later_invoice.refresh_from_db()
            self.assertTrue(self.line.waived)
            self.assertEqual(later_invoice.total, Decimal('1000000.00'))

    def test_waiver_outside_validity_window_does_not_touch_out_of_range_invoice(self):
        with tenant_context(self.foundation.id):
            disc = Discount.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                fee_type=self.fee_spp,
                type=DiscountType.FIXED,
                value=Decimal('1000000.00'),
                reason="Keringanan Khusus Yayasan",
                valid_from=datetime.date(2026, 11, 1),
                status=DiscountStatus.PENDING_APPROVAL,
            )
            approve_discount(disc, user=self.admin_user)

            self.line.refresh_from_db()
            self.invoice.refresh_from_db()
            self.assertFalse(self.line.waived)
            self.assertEqual(self.invoice.total, Decimal('1000000.00'))

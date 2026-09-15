from decimal import Decimal
import datetime
from django.db import connection as default_connection
from django.db.utils import load_backend
from django.test import TestCase

from apps.identity.models import Foundation, Person, School, Student, User, Guardian, GuardianLink
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeePlan,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)
from apps.finance.services import generate_monthly_invoices
from apps.core.locks import advisory_lock
from educore.middleware.tenancy import set_current_foundation_id, clear_current_foundation_id, tenant_context


class InvoiceGenerationTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.999.888.7-000.000",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="30100010",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        # Create Fee Types
        self.fee_spp = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_SMA",
            name="SPP SMA",
            category=FeeCategory.SPP,
            recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('850000.00'),
            currency='IDR',
        )
        self.fee_act = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="UANG_KEGIATAN",
            name="Uang Kegiatan",
            category=FeeCategory.ACTIVITY,
            recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('150050.00'),  # Ending in 50 -> test IDR rounding
            currency='IDR',
        )

        # Active Student 1 (Grade 10)
        self.person1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3271010101010001",
            full_name="Budi Santoso",
        )
        self.student_active1 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person1,
            nisn="0011223344",
            nis="SMA-001",
            status=Student.STATUS_ACTIVE,
        )

        # Active Student 2 with Discount
        self.person2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3271010101010002",
            full_name="Siti Rahma",
        )
        self.student_active2 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person2,
            nisn="0011223355",
            nis="SMA-002",
            status=Student.STATUS_ACTIVE,
        )
        # 10% discount on SPP for student 2
        Discount.objects.create(
            foundation_id=self.foundation.id,
            student=self.student_active2,
            fee_type=self.fee_spp,
            type=DiscountType.PERCENT,
            value=Decimal('10.00'),
            valid_from=datetime.date(2026, 1, 1),
            status=DiscountStatus.APPROVED,
        )

        # Inactive Student 3 (must be excluded per FIN-005)
        self.person3 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3271010101010003",
            full_name="Doni Setiawan",
        )
        self.student_inactive = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person3,
            nisn="0011223366",
            nis="SMA-003",
            status=Student.STATUS_INACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_monthly_generation_with_rounding_and_exclusion(self):
        """Verify batch generation creates invoices with line items, excludes inactive students, and applies PEMBULATAN."""
        period = "2026-10"
        report = generate_monthly_invoices(
            school=self.school,
            period=period,
            dry_run=False,
        )

        self.assertFalse(report['dry_run'])
        self.assertEqual(report['total_active_students'], 2)
        self.assertEqual(report['generated_count'], 2)
        self.assertEqual(report['skipped_existing_count'], 0)

        # Exclusions contains student_inactive (FIN-005)
        self.assertEqual(len(report['exclusions']), 1)
        self.assertEqual(report['exclusions'][0]['student_id'], self.student_inactive.id)

        # Verify Student 1 Invoice:
        # Base: 850,000 + 150,050 = 1,000,050.00
        # Rounding (PEMBULATAN): +50.00 -> Total: 1,000,100.00
        inv1 = Invoice.objects.filter(student=self.student_active1, period=period).first()
        self.assertIsNotNone(inv1)
        self.assertEqual(inv1.number, "INV/30100010/2026/000001")
        self.assertEqual(inv1.subtotal, Decimal('1000050.00'))
        self.assertEqual(inv1.rounding, Decimal('50.00'))
        self.assertEqual(inv1.total, Decimal('1000100.00'))
        self.assertEqual(inv1.status, InvoiceStatus.ISSUED)
        self.assertFalse(inv1.is_overdue)

        lines1 = inv1.lines.all()
        # SPP line, Activity line, Pembulatan line
        self.assertEqual(lines1.count(), 3)
        pembulatan_line = lines1.filter(code='PEMBULATAN').first()
        self.assertIsNotNone(pembulatan_line)
        self.assertEqual(pembulatan_line.amount, Decimal('50.00'))

        # Verify Student 2 Invoice with 10% discount:
        # SPP: 850,000 -> disc 10% = 85,000 -> net 765,000
        # Act: 150,050 -> disc 0 -> net 150,050
        # Net before rounding: 765,000 + 150,050 = 915,050.00
        # Rounding: +50.00 -> Total: 915,100.00
        inv2 = Invoice.objects.filter(student=self.student_active2, period=period).first()
        self.assertIsNotNone(inv2)
        self.assertEqual(inv2.discount, Decimal('85000.00'))
        self.assertEqual(inv2.rounding, Decimal('50.00'))
        self.assertEqual(inv2.total, Decimal('915100.00'))

    def test_idempotency_prevents_duplicate_invoices(self):
        """Verify re-running invoice generation for same period creates zero duplicate invoices (FIN-003)."""
        period = "2026-10"
        # Run 1
        run1 = generate_monthly_invoices(school=self.school, period=period, dry_run=False)
        self.assertEqual(run1['generated_count'], 2)
        self.assertEqual(run1['skipped_existing_count'], 0)

        # Run 2 for same period
        run2 = generate_monthly_invoices(school=self.school, period=period, dry_run=False)
        self.assertEqual(run2['generated_count'], 0)
        self.assertEqual(run2['skipped_existing_count'], 2)

        # Invoices count in DB remains 2
        self.assertEqual(Invoice.objects.filter(period=period).count(), 2)

    def test_dry_run_preview_mode(self):
        """Verify dry-run mode returns calculation totals without writing invoices to DB (FIN-008)."""
        period = "2026-11"
        report = generate_monthly_invoices(school=self.school, period=period, dry_run=True)

        self.assertTrue(report['dry_run'])
        self.assertEqual(report['generated_count'], 2)
        self.assertEqual(len(report['invoices']), 2)

        # Zero invoices created in database
        self.assertEqual(Invoice.objects.filter(period=period).count(), 0)

    def test_advisory_lock_concurrency_prevention(self):
        """Verify advisory lock prevents overlapping concurrent generation runs (spec/06 §9.9)."""
        with advisory_lock('generate_invoices', timeout=0) as acquired1:
            self.assertTrue(acquired1)
            # A second, genuinely separate session must fail to acquire the lock.
            # MySQL's GET_LOCK lets the SAME session re-acquire a name it
            # already holds (non-blocking since MySQL 5.7.5), so this needs an
            # actually different connection to exercise real cross-process
            # rejection — not just a second call on the one connection
            # Django's TestCase reuses for the whole test.
            backend = load_backend(default_connection.settings_dict['ENGINE'])
            conn2 = backend.DatabaseWrapper(default_connection.settings_dict, alias='invoice_lock_test_second')
            try:
                with advisory_lock('generate_invoices', timeout=0, db_connection=conn2) as acquired2:
                    self.assertFalse(acquired2)
            finally:
                conn2.close()

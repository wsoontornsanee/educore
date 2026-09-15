from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import Foundation, Person, School, Student
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeePlan,
    FeeRecurrence,
    FeeType,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class FeeModelsTests(TestCase):
    def setUp(self):
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa",
            brand_name="Bina Bangsa",
            npwp="01.234.567.8-001.000",
            address="Jakarta",
        )
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Insan Mulia",
            brand_name="Insan Mulia",
            npwp="01.234.567.8-002.000",
            address="Surabaya",
        )
        set_current_foundation_id(self.foundation_a.id)
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation_a.id,
            name="SMA Bina Bangsa",
            npsn="20500001",
            level=School.LEVEL_SMA,
        )

    def test_fee_type_creation_and_tenancy_isolation(self):
        with tenant_context(self.foundation_a.id):
            fee = FeeType.objects.create(
                foundation_id=self.foundation_a.id,
                school=self.school_a,
                code="SPP_REGULER",
                name="SPP Bulanan Reguler",
                category=FeeCategory.SPP,
                recurrence=FeeRecurrence.MONTHLY,
                default_amount=Decimal('850000.00'),
                currency="IDR",
            )
            self.assertEqual(FeeType.objects.count(), 1)

        # Cross-tenant isolation: Foundation B sees none
        with tenant_context(self.foundation_b.id):
            self.assertEqual(FeeType.objects.count(), 0)

    def test_fee_type_unique_code_per_school(self):
        with tenant_context(self.foundation_a.id):
            FeeType.objects.create(
                foundation_id=self.foundation_a.id,
                school=self.school_a,
                code="SPP_REGULER",
                name="SPP Bulanan Reguler",
                default_amount=Decimal('850000.00'),
            )
            with self.assertRaises(IntegrityError):
                FeeType.objects.create(
                    foundation_id=self.foundation_a.id,
                    school=self.school_a,
                    code="SPP_REGULER",
                    name="Duplicate SPP",
                    default_amount=Decimal('900000.00'),
                )

    def test_money_field_rejects_floats(self):
        with tenant_context(self.foundation_a.id):
            # Float assignment to MoneyField must raise ValidationError per CUR-005
            fee = FeeType(
                foundation_id=self.foundation_a.id,
                school=self.school_a,
                code="BIAYA_LAB",
                name="Biaya Praktikum",
                default_amount=150000.50,  # float
            )
            with self.assertRaises(ValidationError):
                fee.full_clean()

    def test_fee_plan_and_lines_storage(self):
        with tenant_context(self.foundation_a.id):
            plan = FeePlan.objects.create(
                foundation_id=self.foundation_a.id,
                school=self.school_a,
                academic_year="2026/2027",
                name="Paket Biaya Kelas 10",
                grade_levels=["10"],
                lines=[
                    {"fee_type_code": "SPP_REGULER", "amount": "850000.00", "currency": "IDR"},
                    {"fee_type_code": "KEGIATAN", "amount": "200000.00", "currency": "IDR"},
                ]
            )
            self.assertEqual(plan.academic_year, "2026/2027")
            self.assertEqual(len(plan.lines), 2)

from decimal import Decimal
from django.test import TestCase
from apps.identity.models import Foundation, Person, School, Student
from apps.finance.models import (
    FeeAssignmentSource,
    FeeCategory,
    FeePlan,
    FeeType,
    StudentFeeAssignment,
)
from apps.finance.services import resolve_student_fee_schedule
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class FeeResolutionTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.234.567.8-123.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Harapan Bangsa",
            npsn="20600001",
            level=School.LEVEL_SMP,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010099",
            full_name="Muhammad Fatih",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nisn="0099887766",
            nis="26001",
        )
        self.fee_spp = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_SMP",
            name="SPP SMP Reguler",
            category=FeeCategory.SPP,
            default_amount=Decimal('750000.00'),
        )

    def test_grade_level_plan_resolution(self):
        with tenant_context(self.foundation.id):
            FeePlan.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                academic_year="2026/2027",
                name="Paket Kelas 7",
                grade_levels=["7"],
                lines=[
                    {"fee_type_id": self.fee_spp.id, "amount": "800000.00", "currency": "IDR"}
                ]
            )

            schedule = resolve_student_fee_schedule(self.student, period="2026-10")
            self.assertEqual(len(schedule), 1)
            self.assertEqual(schedule[0]['base_amount'], Decimal('800000.00'))
            self.assertEqual(schedule[0]['source'], FeeAssignmentSource.GRADE)

    def test_student_override_takes_precedence_over_grade_plan(self):
        with tenant_context(self.foundation.id):
            # Grade plan sets 800,000
            FeePlan.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                academic_year="2026/2027",
                name="Paket Kelas 7",
                grade_levels=["7"],
                lines=[
                    {"fee_type_id": self.fee_spp.id, "amount": "800000.00", "currency": "IDR"}
                ]
            )

            # Direct student override sets 650,000 (scholarship/custom fee)
            StudentFeeAssignment.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                fee_type=self.fee_spp,
                amount=Decimal('650000.00'),
                start_period="2026-07",
                end_period="2027-06",
                source=FeeAssignmentSource.STUDENT,
            )

            # Precedence: Student override wins (FIN-001)
            schedule = resolve_student_fee_schedule(self.student, period="2026-10")
            self.assertEqual(len(schedule), 1)
            self.assertEqual(schedule[0]['base_amount'], Decimal('650000.00'))
            self.assertEqual(schedule[0]['source'], FeeAssignmentSource.STUDENT)

    def test_expired_assignment_falls_back_to_grade_or_default(self):
        with tenant_context(self.foundation.id):
            # Direct student assignment expired in 2026-06
            StudentFeeAssignment.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                fee_type=self.fee_spp,
                amount=Decimal('500000.00'),
                start_period="2025-07",
                end_period="2026-06",
                source=FeeAssignmentSource.STUDENT,
            )

            # For period 2026-10, expired assignment is not applied; falls back to default 750,000
            schedule = resolve_student_fee_schedule(self.student, period="2026-10")
            self.assertEqual(len(schedule), 1)
            self.assertEqual(schedule[0]['base_amount'], Decimal('750000.00'))

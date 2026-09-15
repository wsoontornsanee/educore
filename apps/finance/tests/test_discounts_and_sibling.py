import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeeType,
    SiblingDiscountPolicy,
)
from apps.finance.services import (
    approve_discount,
    calculate_sibling_discount,
    create_discount_with_approval_check,
    get_student_child_order,
    resolve_student_fee_schedule,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class DiscountsAndSiblingEngineTests(TestCase):
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
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Pak Hartono",
            nik="3374010101800001",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
        )

        # 3 Sibling children
        # Child 1 (born 2014)
        self.p1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Pertama",
            dob=datetime.date(2014, 3, 15),
        )
        self.child1 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.p1,
            nisn="1000000001",
            nis="SD-01",
            status=Student.STATUS_ACTIVE,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.child1,
            is_primary=True,
            financial_responsible=True,
        )

        # Child 2 (born 2016)
        self.p2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Kedua",
            dob=datetime.date(2016, 7, 20),
        )
        self.child2 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.p2,
            nisn="1000000002",
            nis="SD-02",
            status=Student.STATUS_ACTIVE,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.child2,
            is_primary=True,
            financial_responsible=True,
        )

        # Child 3 (born 2018)
        self.p3 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Anak Ketiga",
            dob=datetime.date(2018, 11, 5),
        )
        self.child3 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.p3,
            nisn="1000000003",
            nis="SD-03",
            status=Student.STATUS_ACTIVE,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.child3,
            is_primary=True,
            financial_responsible=True,
        )

        # SPP fee type
        self.fee_spp = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_SD",
            name="SPP SD",
            category=FeeCategory.SPP,
            default_amount=Decimal('1000000.00'),
        )

        # Policies: 2nd child -> 10%, 3rd child -> 20%
        SiblingDiscountPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            child_order=2,
            discount_percent=Decimal('10.00'),
            fee_category=FeeCategory.SPP,
        )
        SiblingDiscountPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            child_order=3,
            discount_percent=Decimal('20.00'),
            fee_category=FeeCategory.SPP,
        )

    def test_child_order_determination(self):
        with tenant_context(self.foundation.id):
            self.assertEqual(get_student_child_order(self.child1), 1)
            self.assertEqual(get_student_child_order(self.child2), 2)
            self.assertEqual(get_student_child_order(self.child3), 3)

    def test_automatic_sibling_discount_calculation(self):
        with tenant_context(self.foundation.id):
            # Child 1: 0% discount -> full 1,000,000
            disc1 = calculate_sibling_discount(self.child1, self.fee_spp, Decimal('1000000.00'))
            self.assertEqual(disc1, Decimal('0.00'))

            # Child 2: 10% discount -> 100,000
            disc2 = calculate_sibling_discount(self.child2, self.fee_spp, Decimal('1000000.00'))
            self.assertEqual(disc2, Decimal('1000000.00') * Decimal('0.10'))
            self.assertEqual(disc2, Decimal('100000.00'))

            # Child 3: 20% discount -> 200,000
            disc3 = calculate_sibling_discount(self.child3, self.fee_spp, Decimal('1000000.00'))
            self.assertEqual(disc3, Decimal('200000.00'))

            # Schedule integration for Child 2: final amount = 900,000
            schedule2 = resolve_student_fee_schedule(self.child2, period="2026-10")
            self.assertEqual(schedule2[0]['discount_amount'], Decimal('100000.00'))
            self.assertEqual(schedule2[0]['final_amount'], Decimal('900000.00'))

    def test_approval_threshold_for_high_value_discounts(self):
        with tenant_context(self.foundation.id):
            # 1. Normal discount (15% <= 25%) -> Auto-APPROVED
            disc_normal = create_discount_with_approval_check(
                foundation_id=self.foundation.id,
                student=self.child1,
                type=DiscountType.PERCENT,
                value=Decimal('15.00'),
                reason="Diskon Prestasi",
                valid_from=datetime.date(2026, 7, 1),
                user=self.admin_user,
            )
            self.assertEqual(disc_normal.status, DiscountStatus.APPROVED)
            self.assertIsNotNone(disc_normal.approved_at)

            # 2. High-value waiver (Rp 2,500,000 > Rp 1,000,000) -> PENDING_APPROVAL (FIN-007)
            disc_high = create_discount_with_approval_check(
                foundation_id=self.foundation.id,
                student=self.child1,
                type=DiscountType.FIXED,
                value=Decimal('2500000.00'),
                reason="Keringanan Khusus Yayasan",
                valid_from=datetime.date(2026, 7, 1),
                user=self.admin_user,
            )
            self.assertEqual(disc_high.status, DiscountStatus.PENDING_APPROVAL)
            self.assertIsNone(disc_high.approved_at)

            # 3. Approve high discount
            approve_discount(disc_high, user=self.admin_user)
            disc_high.refresh_from_db()
            self.assertEqual(disc_high.status, DiscountStatus.APPROVED)
            self.assertIsNotNone(disc_high.approved_at)

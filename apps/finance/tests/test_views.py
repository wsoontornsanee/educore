import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeePlan,
    FeeType,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class FinanceViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cendekia",
            brand_name="Insan Cendekia",
            npwp="01.234.567.8-555.000",
            address="Yogyakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Insan Cendekia",
            npsn="20800001",
            level=School.LEVEL_SMA,
        )
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628177777777",
            email="bendahara@cendekia.sch.id",
            full_name="Bendahara Sekolah",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role="finance_officer",
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101010001",
            full_name="Santri Ahmad",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nisn="1122334455",
            nis="SMA-01",
        )

    def test_fee_type_crud_api(self):
        self.client.force_authenticate(user=self.finance_user)
        # Create
        data = {
            'school': self.school.id,
            'code': 'SPP_REG',
            'name': 'SPP Reguler',
            'category': 'SPP',
            'recurrence': 'MONTHLY',
            'default_amount': '950000.00',
            'currency': 'IDR',
        }
        res = self.client.post(f'/api/v1/finance/fee-types/?school_id={self.school.id}', data, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['code'], 'SPP_REG')
        self.assertEqual(res.json()['default_amount'], '950000.00')

        # List
        res_list = self.client.get(f'/api/v1/finance/fee-types/?school_id={self.school.id}')
        self.assertEqual(res_list.status_code, 200)
        results = res_list.json().get('results', res_list.json())
        self.assertEqual(len(results), 1)

    def test_fee_plan_assignment_action(self):
        self.client.force_authenticate(user=self.finance_user)
        with tenant_context(self.foundation.id):
            fee = FeeType.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                code="SPP_TEST",
                name="SPP Test",
                default_amount=Decimal('800000.00'),
            )
            plan = FeePlan.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                academic_year="2026/2027",
                name="Paket Kelas 10",
                grade_levels=["10"],
                lines=[{"fee_type_id": fee.id, "amount": "800000.00", "currency": "IDR"}],
            )

        assign_payload = {
            'student_ids': [self.student.id],
            'start_period': '2026-07',
        }
        res = self.client.post(f'/api/v1/finance/fee-plans/{plan.id}/assign/?school_id={self.school.id}', assign_payload, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['assigned'], 1)

        # Verify StudentFeeAssignment created
        with tenant_context(self.foundation.id):
            ass = StudentFeeAssignment.objects.filter(student=self.student).first()
            self.assertIsNotNone(ass)
            self.assertEqual(ass.amount, Decimal('800000.00'))

    def test_discount_approval_action(self):
        self.client.force_authenticate(user=self.finance_user)
        with tenant_context(self.foundation.id):
            discount = Discount.objects.create(
                foundation_id=self.foundation.id,
                student=self.student,
                type=DiscountType.FIXED,
                value=Decimal('1500000.00'),
                reason="Permohonan Keringanan",
                valid_from=datetime.date(2026, 7, 1),
                status=DiscountStatus.PENDING_APPROVAL,
            )

        # Approve discount
        res = self.client.post(f'/api/v1/finance/discounts/{discount.id}/approve/?school_id={self.school.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], DiscountStatus.APPROVED)

        discount.refresh_from_db()
        self.assertEqual(discount.status, DiscountStatus.APPROVED)
        self.assertEqual(discount.approved_by, self.finance_user)

    def test_sibling_discount_policy_crud(self):
        self.client.force_authenticate(user=self.finance_user)
        policy_data = {
            'school': self.school.id,
            'child_order': 2,
            'discount_percent': '15.00',
            'fee_category': 'SPP',
            'is_active': True,
        }
        res = self.client.post(f'/api/v1/finance/sibling-policies/?school_id={self.school.id}', policy_data, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['discount_percent'], '15.00')

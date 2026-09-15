from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.finance.models import (
    ConvenienceFeeAllocation,
    ConvenienceFeeType,
    Invoice,
    InvoiceStatus,
    PaymentMethod,
    SchoolConvenienceFeePolicy,
)
from apps.finance.serializers import PaymentIntentSerializer as PISerializer
from apps.finance.services import (
    calculate_convenience_fee,
    create_payment_intent,
    get_effective_convenience_fee_policy,
    set_school_convenience_fee_policy,
)
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def build_fixture(foundation_name="Yayasan Convenience Fee Test"):
    clear_current_foundation_id()
    foundation = Foundation.objects.create(
        legal_name=foundation_name, brand_name=foundation_name, npwp="01.222.333.4-555.000", address="Bandung",
    )
    set_current_foundation_id(foundation.id)
    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id, name="SD Convenience Fee Test", npsn=f"401{npsn_suffix}", level=School.LEVEL_SD, base_currency="IDR",
    )
    person = Person.all_tenants.create(foundation_id=foundation.id, nik=f"34720101{npsn_suffix}", full_name="Siswa CF")
    student = Student.all_tenants.create(
        foundation_id=foundation.id, school=school, person=person, nisn=npsn_suffix, nis=f"CF-{npsn_suffix}", status=Student.STATUS_ACTIVE,
    )
    finance_user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+6282{npsn_suffix}", email=f"finance.{npsn_suffix}@cf.school.id", full_name="Bendahara CF",
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=finance_user, role=RoleAssignment.ROLE_FINANCE_OFFICER,
        scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=foundation.id,
    )
    invoice = Invoice.objects.create(
        foundation_id=foundation.id,
        school=school,
        student=student,
        number=f"INV/{school.npsn}/2026/000001",
        period="2026-11",
        due_date="2026-11-10",
        subtotal=Decimal('500000.00'),
        total=Decimal('500000.00'),
        currency='IDR',
        status=InvoiceStatus.ISSUED,
    )
    return {'foundation': foundation, 'school': school, 'student': student, 'finance_user': finance_user, 'invoice': invoice}


class GetEffectiveConvenienceFeePolicyTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_default_when_unconfigured(self):
        policy = get_effective_convenience_fee_policy(self.fx['school'])
        self.assertEqual(policy['allocation'], ConvenienceFeeAllocation.PASSED_TO_PARENT)
        self.assertEqual(policy['fee_value'], Decimal('0.00'))

    def test_explicit_override(self):
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.ABSORBED_BY_SCHOOL,
            fee_type=ConvenienceFeeType.FIXED, fee_value=Decimal('2500.00'),
        )
        policy = get_effective_convenience_fee_policy(self.fx['school'])
        self.assertEqual(policy['allocation'], ConvenienceFeeAllocation.ABSORBED_BY_SCHOOL)
        self.assertEqual(policy['fee_value'], Decimal('2500.00'))

    def test_inactive_policy_falls_back_to_default(self):
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.PASSED_TO_PARENT,
            fee_value=Decimal('9999.00'), is_active=False,
        )
        policy = get_effective_convenience_fee_policy(self.fx['school'])
        self.assertEqual(policy['fee_value'], Decimal('0.00'))

    def test_only_one_policy_row_per_school(self):
        set_school_convenience_fee_policy(self.fx['school'], fee_value=Decimal('1000.00'))
        set_school_convenience_fee_policy(self.fx['school'], fee_value=Decimal('2000.00'))
        self.assertEqual(SchoolConvenienceFeePolicy.all_tenants.filter(school=self.fx['school']).count(), 1)


class CalculateConvenienceFeeTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_zero_when_unconfigured(self):
        self.assertEqual(calculate_convenience_fee(self.fx['school'], Decimal('500000.00')), Decimal('0.00'))

    def test_fixed_fee_passed_to_parent(self):
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.PASSED_TO_PARENT,
            fee_type=ConvenienceFeeType.FIXED, fee_value=Decimal('3000.00'),
        )
        self.assertEqual(calculate_convenience_fee(self.fx['school'], Decimal('500000.00')), Decimal('3000.00'))

    def test_percentage_fee_rounds_half_up(self):
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.PASSED_TO_PARENT,
            fee_type=ConvenienceFeeType.PERCENTAGE, fee_value=Decimal('1.5'),
        )
        # 500000 * 1.5% = 7500.00 exactly
        self.assertEqual(calculate_convenience_fee(self.fx['school'], Decimal('500000.00')), Decimal('7500.00'))

    def test_absorbed_by_school_yields_zero_even_with_fee_value_set(self):
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.ABSORBED_BY_SCHOOL,
            fee_type=ConvenienceFeeType.FIXED, fee_value=Decimal('3000.00'),
        )
        self.assertEqual(calculate_convenience_fee(self.fx['school'], Decimal('500000.00')), Decimal('0.00'))


class CreatePaymentIntentConvenienceFeeIntegrationTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()
        set_school_convenience_fee_policy(
            self.fx['school'], allocation=ConvenienceFeeAllocation.PASSED_TO_PARENT,
            fee_type=ConvenienceFeeType.FIXED, fee_value=Decimal('3000.00'),
        )

    def test_fee_added_to_va_intent_amount(self):
        intent = create_payment_intent(
            school=self.fx['school'], student=self.fx['student'], invoice_ids=[self.fx['invoice'].id],
            method=PaymentMethod.VA, bank='BCA',
        )
        self.assertEqual(intent.amount, Decimal('503000.00'))
        self.assertEqual(intent.metadata['base_amount'], '500000.00')
        self.assertEqual(intent.metadata['convenience_fee_amount'], '3000.00')

    def test_fee_embedded_in_qris_amount(self):
        intent = create_payment_intent(
            school=self.fx['school'], student=self.fx['student'], invoice_ids=[self.fx['invoice'].id],
            method=PaymentMethod.QRIS,
        )
        self.assertEqual(intent.amount, Decimal('503000.00'))

    def test_no_fee_for_manual_transfer(self):
        intent = create_payment_intent(
            school=self.fx['school'], student=self.fx['student'], invoice_ids=[self.fx['invoice'].id],
            method=PaymentMethod.MANUAL,
        )
        self.assertEqual(intent.amount, Decimal('500000.00'))
        self.assertEqual(intent.metadata['convenience_fee_amount'], '0.00')

    def test_serializer_exposes_fee_breakdown(self):
        intent = create_payment_intent(
            school=self.fx['school'], student=self.fx['student'], invoice_ids=[self.fx['invoice'].id],
            method=PaymentMethod.VA, bank='BCA',
        )
        data = PISerializer(intent).data
        self.assertEqual(data['base_amount'], '500000.00')
        self.assertEqual(data['convenience_fee_amount'], '3000.00')
        self.assertEqual(data['amount'], '503000.00')


class SchoolConvenienceFeePolicyViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_fixture()

    def test_get_returns_platform_default_when_unset(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/finance/schools/{self.fx["school"].id}/convenience-fee-policy/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['allocation'], ConvenienceFeeAllocation.PASSED_TO_PARENT)
        self.assertEqual(res.json()['fee_value'], '0.00')

    def test_put_then_get_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(
            f'/api/v1/finance/schools/{self.fx["school"].id}/convenience-fee-policy/',
            {'allocation': ConvenienceFeeAllocation.PASSED_TO_PARENT, 'fee_type': ConvenienceFeeType.FIXED, 'fee_value': '2500.00'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)

        res2 = self.client.get(f'/api/v1/finance/schools/{self.fx["school"].id}/convenience-fee-policy/')
        self.assertEqual(res2.json()['fee_value'], '2500.00')

    def test_cross_tenant_school_404(self):
        other_fx = build_fixture(foundation_name="Yayasan Convenience Fee Other")
        self.client.force_authenticate(user=self.fx['finance_user'])
        set_current_foundation_id(self.fx['foundation'].id)
        res = self.client.get(f'/api/v1/finance/schools/{other_fx["school"].id}/convenience-fee-policy/')
        self.assertEqual(res.status_code, 404)

"""Tests for the Foundation Approvals Inbox (FND-007/008/009, spec/03 §2/§5)."""
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.finance.models import Discount, DiscountStatus, DiscountType, Payment, PaymentMethod, PaymentStatus, Refund, RefundStatus
from apps.finance.services.invoicing import create_discount_with_approval_check
from apps.finance.services.refunds import request_refund
from apps.identity.models import Foundation, Person, School, Student, User
from apps.identity.rbac import (
    assign_role, ROLE_FINANCE_OFFICER, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER,
    SCOPE_FOUNDATION, SCOPE_SCHOOL,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class FoundationApprovalsInboxTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa", brand_name="Bina Bangsa", npwp="01.888.777.6-001.000",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Bina Bangsa", npsn="40100001",
            level=School.LEVEL_SMA, base_currency="IDR",
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Budi Santoso", nik="3171010101010099",
            gender=Person.GENDER_MALE, dob="2010-01-01",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person, status=Student.STATUS_ACTIVE,
        )

        self.staff = User.all_tenants.create_user(
            foundation_id=self.foundation.id, phone_e164="+6281299990010", full_name="Staff Keuangan",
        )
        self.admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id, phone_e164="+6281299990011", full_name="Ketua Yayasan",
        )
        assign_role(
            user=self.admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )
        self.school_admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id, phone_e164="+6281299990012", full_name="Admin Sekolah",
        )
        assign_role(
            user=self.school_admin, role=ROLE_SCHOOL_ADMIN, scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id, foundation_id=self.foundation.id,
        )
        self.teacher = User.all_tenants.create_user(
            foundation_id=self.foundation.id, phone_e164="+6281299990013", full_name="Guru",
        )
        assign_role(
            user=self.teacher, role=ROLE_TEACHER, scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id, foundation_id=self.foundation.id,
        )
        self.finance_officer = User.all_tenants.create_user(
            foundation_id=self.foundation.id, phone_e164="+6281299990014", full_name="Staf Keuangan Sekolah",
        )
        assign_role(
            user=self.finance_officer, role=ROLE_FINANCE_OFFICER, scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id, foundation_id=self.foundation.id,
        )

        # A pending PERCENT discount (>25% threshold) -> reported as type='discount'.
        self.pending_discount = create_discount_with_approval_check(
            foundation_id=self.foundation.id, student=self.student, type=DiscountType.PERCENT,
            value=Decimal('30.00'), reason="Beasiswa Prestasi", valid_from="2026-01-01", user=self.staff,
        )
        # A pending FIXED discount (> Rp 1,000,000 threshold) -> reported as type='waiver'.
        self.pending_waiver = create_discount_with_approval_check(
            foundation_id=self.foundation.id, student=self.student, type=DiscountType.FIXED,
            value=Decimal('2000000.00'), reason="Keringanan Biaya Pendaftaran", valid_from="2026-01-01", user=self.staff,
        )

        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            amount=Decimal('3000000.00'), fee=Decimal('0.00'), net=Decimal('3000000.00'), currency='IDR',
            method=PaymentMethod.MANUAL, channel='MANUAL_TRANSFER', reference='PAY/40100001/2026/000001',
            status=PaymentStatus.SETTLED, settled_at=timezone.now(),
        )
        self.pending_refund = request_refund(
            payment=self.payment, amount=Decimal('1500000.00'), reason="Pembatalan pendaftaran",
            destination_bank_name="BCA", destination_account_number="1234567890",
            destination_account_holder="Budi Santoso", requested_by=self.staff,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_lists_all_pending_approvals_across_types(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals?status=pending')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            ids = {item['id'] for item in response.data['results']}
            self.assertEqual(
                ids,
                {f'discount:{self.pending_discount.id}', f'waiver:{self.pending_waiver.id}', f'refund:{self.pending_refund.id}'},
            )

    def test_filter_by_type_discount_excludes_waiver(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals?type=discount')
            ids = {item['id'] for item in response.data['results']}
            self.assertEqual(ids, {f'discount:{self.pending_discount.id}'})
            self.assertEqual(response.data['results'][0]['type'], 'discount')

    def test_filter_by_type_waiver_excludes_discount(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals?type=waiver')
            ids = {item['id'] for item in response.data['results']}
            self.assertEqual(ids, {f'waiver:{self.pending_waiver.id}'})
            self.assertEqual(response.data['results'][0]['type'], 'waiver')

    def test_filter_by_type_payroll_returns_empty(self):
        """No payroll domain model exists yet -- a recognized, always-empty type."""
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals?type=payroll')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['results'], [])

    def test_filter_by_unknown_type_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals?type=bogus')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_forbidden_from_inbox(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/approvals')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_decide_approve_discount(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['status'], DiscountStatus.APPROVED)
            self.assertEqual(response.data['requested_by'], str(self.staff.id))
        self.pending_discount.refresh_from_db()
        self.assertEqual(self.pending_discount.status, DiscountStatus.APPROVED)
        self.assertEqual(self.pending_discount.approved_by_id, self.admin.id)
        self.assertEqual(self.pending_discount.created_by, str(self.staff.id))

    def test_decide_approve_already_rejected_discount_returns_400(self):
        """approve_discount only guards against re-approving; it must also
        refuse a discount that isn't PENDING_APPROVAL at all (e.g. already
        REJECTED), matching reject_discount's own state guard."""
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            reject_response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'REJECT', 'reason': 'Tidak memenuhi kriteria'}, format='json',
            )
            self.assertEqual(reject_response.status_code, status.HTTP_200_OK)

            approve_response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(approve_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.pending_discount.refresh_from_db()
        self.assertEqual(self.pending_discount.status, DiscountStatus.REJECTED)

    def test_decide_type_prefix_must_match_actual_discount_type(self):
        """The composite id's prefix must match the row's real PERCENT/FIXED
        type -- 'discount:<id>' against a FIXED (waiver) row is treated as
        not found, not silently decided under the wrong label."""
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_waiver.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_decide_reject_discount_requires_reason(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/waiver:{self.pending_waiver.id}/decide',
                data={'decision': 'REJECT'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

            response = self.client.post(
                f'/api/v1/foundation/approvals/waiver:{self.pending_waiver.id}/decide',
                data={'decision': 'REJECT', 'reason': 'Dokumen tidak lengkap'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['status'], DiscountStatus.REJECTED)

    def test_decide_approve_refund(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/refund:{self.pending_refund.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data['status'], RefundStatus.APPROVED)

    def test_decide_by_school_admin_forbidden_at_rbac_layer(self):
        """School admins lack finance.invoice.write -- blocked by the view's
        own RBAC gate before ever reaching the finance service layer."""
        self.client.force_authenticate(user=self.school_admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_decide_by_finance_officer_forbidden_at_service_layer(self):
        """Finance officers hold finance.invoice.write (pass the view's RBAC
        gate) but are not foundation admins -- the underlying service must
        still reject them (FND-007's 'requires foundation approval'), the
        defense-in-depth this view's docstring describes."""
        self.client.force_authenticate(user=self.finance_officer)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_decide_malformed_id_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                '/api/v1/foundation/approvals/not-a-valid-id/decide', data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_decide_unknown_id_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                '/api/v1/foundation/approvals/discount:999999/decide', data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_decide_payroll_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post(
                '/api/v1/foundation/approvals/payroll:1/decide', data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_decide_cross_tenant_returns_404(self):
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="02.111.222.3-001.000",
        )
        other_admin = User.all_tenants.create_user(
            foundation_id=other_foundation.id, phone_e164="+6281299990099", full_name="Admin Lain",
        )
        assign_role(
            user=other_admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=other_foundation.id, foundation_id=other_foundation.id,
        )
        self.client.force_authenticate(user=other_admin)
        with tenant_context(other_foundation.id):
            response = self.client.post(
                f'/api/v1/foundation/approvals/discount:{self.pending_discount.id}/decide',
                data={'decision': 'APPROVE'}, format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

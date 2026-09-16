import datetime
from decimal import Decimal
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import TenantModel
from apps.identity.models import (
    Foundation,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.identity.rbac import is_foundation_admin
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeCategory,
    FeePlan,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    InvoiceWriteOffStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundStatus,
)
from apps.finance.services import (
    approve_discount,
    reject_discount,
    approve_invoice_write_off,
    reject_invoice_write_off,
    request_invoice_write_off,
    approve_refund,
    request_refund,
    create_discount_with_approval_check,
)
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class ApprovalRoleVerificationTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.999.888.7-111.000",
            address="Jakarta Selatan",
        )
        self.foundation2 = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Yayasan Lain",
            npwp="02.999.888.7-111.000",
            address="Bandung",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="20109999",
            level=School.LEVEL_SMA,
        )

        # Users
        self.super_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628111111101",
            email="root@harapan.sch.id",
            full_name="Super Admin",
            is_superuser=True,
        )
        self.foundation_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628111111102",
            email="fndadmin@harapan.sch.id",
            full_name="Yayasan Admin",
        )
        RoleAssignment.all_tenants.create(
            user=self.foundation_admin,
            foundation_id=self.foundation.id,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.finance_officer = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628111111103",
            email="finance@harapan.sch.id",
            full_name="Petugas Keuangan Sekolah",
        )
        RoleAssignment.all_tenants.create(
            user=self.finance_officer,
            foundation_id=self.foundation.id,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

        self.other_fnd_admin = User.objects.create(
            foundation_id=self.foundation2.id,
            phone_e164="+628111111104",
            email="admin@other.org",
            full_name="Admin Yayasan Lain",
        )
        RoleAssignment.all_tenants.create(
            user=self.other_fnd_admin,
            foundation_id=self.foundation2.id,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation2.id,
        )

        # Student & Fee Data
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Siswa Berbakat",
            dob=datetime.date(2008, 1, 1),
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="2026001",
            nisn="0081234567",
        )
        self.fee_type = FeeType.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            name="SPP Bulanan",
            category=FeeCategory.SPP,
        )

        # Client
        self.client = APIClient()

    def test_is_foundation_admin_helper(self):
        """Verify is_foundation_admin helper behavior across roles and foundations."""
        with tenant_context(self.foundation.id):
            self.assertTrue(is_foundation_admin(self.super_user, self.foundation.id))
            self.assertTrue(is_foundation_admin(self.foundation_admin, self.foundation.id))
            self.assertFalse(is_foundation_admin(self.finance_officer, self.foundation.id))
            # Admin of Foundation B cannot act as admin of Foundation A
            self.assertFalse(is_foundation_admin(self.other_fnd_admin, self.foundation.id))

    def test_discount_approval_requires_foundation_admin(self):
        """School finance officer cannot approve or reject discount; Foundation Admin can."""
        with tenant_context(self.foundation.id):
            # Create high value discount pending approval
            disc = create_discount_with_approval_check(
                foundation_id=self.foundation.id,
                student=self.student,
                type=DiscountType.FIXED,
                value=Decimal('2000000.00'),
                reason="Permohonan Beasiswa Penuh",
                valid_from=datetime.date(2026, 7, 1),
                user=self.finance_officer,
            )
            self.assertEqual(disc.status, DiscountStatus.PENDING_APPROVAL)

            # Finance officer attempting approve in service layer -> PermissionDenied
            with self.assertRaises(PermissionDenied):
                approve_discount(disc, user=self.finance_officer)

            # Finance officer attempting reject in service layer -> PermissionDenied
            with self.assertRaises(PermissionDenied):
                reject_discount(disc, user=self.finance_officer, reason="Ditolak")

            # Foundation admin approves -> OK
            approved = approve_discount(disc, user=self.foundation_admin)
            self.assertEqual(approved.status, DiscountStatus.APPROVED)
            self.assertEqual(approved.approved_by, self.foundation_admin)

    def test_discount_approval_view_permissions(self):
        """API: Finance officer gets 403 when calling approve/reject; Foundation Admin gets 200."""
        with tenant_context(self.foundation.id):
            disc = create_discount_with_approval_check(
                foundation_id=self.foundation.id,
                student=self.student,
                type=DiscountType.FIXED,
                value=Decimal('2000000.00'),
                reason="Permohonan Keringanan",
                valid_from=datetime.date(2026, 7, 1),
                user=self.finance_officer,
            )

        # 1. Finance officer tries to approve -> 403 Forbidden
        self.client.force_authenticate(user=self.finance_officer)
        res = self.client.post(f"/api/v1/finance/discounts/{disc.id}/approve/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Finance officer tries to reject -> 403 Forbidden
        res = self.client.post(f"/api/v1/finance/discounts/{disc.id}/reject/", {'reason': 'Ditolak'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Foundation Admin approves -> 200 OK
        self.client.force_authenticate(user=self.foundation_admin)
        res = self.client.post(f"/api/v1/finance/discounts/{disc.id}/approve/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], DiscountStatus.APPROVED)

    def test_invoice_write_off_requires_foundation_admin(self):
        """School finance officer cannot approve/reject write-off; Foundation Admin can."""
        with tenant_context(self.foundation.id):
            invoice = Invoice.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                student=self.student,
                number="INV/2026/09/9991",
                period="2026-09",
                subtotal=Decimal('500000.00'),
                total=Decimal('500000.00'),
                paid=Decimal('0.00'),
                status=InvoiceStatus.ISSUED,
                due_date=datetime.date(2026, 1, 1),
            )
            InvoiceLine.objects.create(
                foundation_id=self.foundation.id,
                invoice=invoice,
                code="SPP",
                description="SPP",
                amount=Decimal('500000.00'),
                subtotal=Decimal('500000.00'),
            )
            req = request_invoice_write_off(
                invoice=invoice,
                user=self.finance_officer,
                reason="Siswa pindah luar negeri dan tidak terlacak",
            )
            self.assertEqual(req.status, InvoiceWriteOffStatus.PENDING)

            # Finance officer calling service -> PermissionDenied
            with self.assertRaises(PermissionDenied):
                approve_invoice_write_off(req, user=self.finance_officer)

            with self.assertRaises(PermissionDenied):
                reject_invoice_write_off(req, user=self.finance_officer)

        # API check
        self.client.force_authenticate(user=self.finance_officer)
        res = self.client.post(f"/api/v1/finance/write-offs/{req.id}/approve/", {'notes': 'Approve'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.post(f"/api/v1/finance/write-offs/{req.id}/reject/", {'notes': 'Reject'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Foundation Admin approves via API -> 200 OK
        self.client.force_authenticate(user=self.foundation_admin)
        res = self.client.post(f"/api/v1/finance/write-offs/{req.id}/approve/", {'notes': 'Disetujui Yayasan'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], InvoiceWriteOffStatus.APPROVED)

    def test_refund_approval_requires_foundation_admin(self):
        """School finance officer cannot approve/reject refund > threshold; Foundation Admin can."""
        with tenant_context(self.foundation.id):
            payment = Payment.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                student=self.student,
                reference="PAY-REF-2026-9999",
                method=PaymentMethod.MANUAL,
                amount=Decimal('2000000.00'),
                currency="IDR",
                status=PaymentStatus.SETTLED,
            )
            refund = request_refund(
                payment=payment,
                amount=Decimal('1500000.00'),
                reason="Kelebihan pembayaran SPP",
                destination_bank_name="BCA",
                destination_account_number="1234567890",
                destination_account_holder="Orang Tua Murid",
                requested_by=self.finance_officer,
            )
            self.assertEqual(refund.status, RefundStatus.PENDING_APPROVAL)

            # Finance officer calling service -> PermissionDenied
            with self.assertRaises(PermissionDenied):
                approve_refund(refund, user=self.finance_officer, decision='APPROVE')

            with self.assertRaises(PermissionDenied):
                approve_refund(refund, user=self.finance_officer, decision='REJECT', reason='Tidak valid')

        # API check: Finance officer tries to approve -> 403 Forbidden
        self.client.force_authenticate(user=self.finance_officer)
        res = self.client.post(
            f"/api/v1/finance/refunds/{refund.id}/approve/",
            {'decision': 'APPROVE'},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Foundation Admin approves via API -> 200 OK
        self.client.force_authenticate(user=self.foundation_admin)
        res = self.client.post(
            f"/api/v1/finance/refunds/{refund.id}/approve/",
            {'decision': 'APPROVE'},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], RefundStatus.APPROVED)

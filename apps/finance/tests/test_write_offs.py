from decimal import Decimal
import datetime
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import TenantModel
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.finance.models import (
    AccountCode,
    FeeCategory,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    InvoiceWriteOffStatus,
    LedgerEntry,
    LedgerJournal,
)
from apps.finance.services import (
    approve_invoice_write_off,
    reject_invoice_write_off,
    request_invoice_write_off,
    write_off_invoice,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class WriteOffWorkflowTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.123.456.7-890.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="20100001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        # Foundation admin user
        self.foundation_admin = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="admin@yayasan.org",
            full_name="Admin Yayasan",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.foundation_admin,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # School finance officer (school scope)
        self.school_finance = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6282222222222",
            email="bendahara@sma.org",
            full_name="Bendahara Sekolah",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.school_finance,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

        # Student & Person
        person = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Siswa Menunggak",
            nik="3171010000000001",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=person,
            nisn="0098765432",
        )

        # Overdue invoice
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            period="2025-01",
            due_date=datetime.date(2025, 1, 10),
            subtotal=Decimal('1000000.00'),
            total=Decimal('1000000.00'),
            paid=Decimal('200000.00'),
            status=InvoiceStatus.PARTIALLY_PAID,
            number="INV/202501/0001",
        )
        InvoiceLine.objects.create(
            foundation_id=self.foundation.id,
            invoice=self.invoice,
            code="SPP",
            description="SPP Januari 2025",
            amount=Decimal('1000000.00'),
            subtotal=Decimal('1000000.00'),
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_request_write_off_pending(self):
        """School finance officer can submit a write-off request in PENDING state."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Siswa pindah luar negeri tanpa melunasi sisa SPP",
            amount=Decimal('800000.00'),
        )
        self.assertEqual(req.status, InvoiceWriteOffStatus.PENDING)
        self.assertEqual(req.amount, Decimal('800000.00'))
        self.assertEqual(req.requested_by, self.school_finance)
        self.assertIsNone(req.approved_by)
        self.assertIsNone(req.resolved_at)
        # Invoice is still PARTIALLY_PAID until approved
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PARTIALLY_PAID)

    def test_request_write_off_exceeds_balance_fails(self):
        """Cannot request write-off greater than invoice remaining balance."""
        with self.assertRaises(ValidationError):
            request_invoice_write_off(
                invoice=self.invoice,
                user=self.school_finance,
                reason="Invalid amount",
                amount=Decimal('800001.00'),  # remaining is 800,000
            )

    def test_request_write_off_on_paid_invoice_fails(self):
        """Cannot request write-off on already paid invoice."""
        self.invoice.status = InvoiceStatus.PAID
        self.invoice.save()
        with self.assertRaises(ValidationError):
            request_invoice_write_off(
                invoice=self.invoice,
                user=self.school_finance,
                reason="Sudah lunas",
            )

    def test_approval_restricted_to_foundation(self):
        """School finance user cannot approve write-off request (FIN-031)."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Keluarga mengalami musibah berat",
        )
        with self.assertRaises(PermissionDenied):
            approve_invoice_write_off(req, self.school_finance)

    def test_rejection_restricted_to_foundation(self):
        """School finance user cannot reject write-off request."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Keluarga mengalami musibah berat",
        )
        with self.assertRaises(PermissionDenied):
            reject_invoice_write_off(req, self.school_finance)

    def test_approval_by_foundation_admin_posts_balanced_ledger(self):
        """Foundation Admin approval marks invoice WRITTEN_OFF and creates Dr 5400 / Cr 1200 journal."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Musibah ekonomi keluarga siswa",
        )
        approved_req = approve_invoice_write_off(
            request_obj=req,
            user=self.foundation_admin,
            notes="Disetujui dewan yayasan setelah verifikasi dokumen",
        )

        self.assertEqual(approved_req.status, InvoiceWriteOffStatus.APPROVED)
        self.assertEqual(approved_req.approved_by, self.foundation_admin)
        self.assertIsNotNone(approved_req.resolved_at)

        # Invoice updated to WRITTEN_OFF
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.WRITTEN_OFF)

        # Ledger journal created
        journal = approved_req.journal
        self.assertIsNotNone(journal)
        self.assertEqual(journal.ref_type, 'WRITE_OFF')

        entries = list(journal.entries.all())
        self.assertEqual(len(entries), 2)

        dr_entry = next(e for e in entries if e.debit > Decimal('0.00'))
        cr_entry = next(e for e in entries if e.credit > Decimal('0.00'))

        self.assertEqual(dr_entry.account_code, AccountCode.BAD_DEBT_EXPENSE)
        self.assertEqual(dr_entry.debit, Decimal('800000.00'))

        self.assertEqual(cr_entry.account_code, AccountCode.ACCOUNTS_RECEIVABLE)
        self.assertEqual(cr_entry.credit, Decimal('800000.00'))

    def test_rejection_leaves_invoice_status_unchanged(self):
        """Foundation Admin rejecting request sets REJECTED and leaves invoice untouched."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Alasan tidak jelas",
        )
        rejected = reject_invoice_write_off(
            request_obj=req,
            user=self.foundation_admin,
            notes="Berkas kurang lengkap, koordinasikan kembali dengan wali murid.",
        )
        self.assertEqual(rejected.status, InvoiceWriteOffStatus.REJECTED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PARTIALLY_PAID)

    def test_cannot_re_resolve_request(self):
        """Cannot approve or reject an already resolved request."""
        req = request_invoice_write_off(
            invoice=self.invoice,
            user=self.school_finance,
            reason="Musibah",
        )
        approve_invoice_write_off(req, self.foundation_admin)
        with self.assertRaises(ValidationError):
            reject_invoice_write_off(req, self.foundation_admin)
        with self.assertRaises(ValidationError):
            approve_invoice_write_off(req, self.foundation_admin)

    def test_direct_write_off_invoice_service(self):
        """write_off_invoice creates auto-approved request and journal when run by foundation admin."""
        written_off = write_off_invoice(
            self.invoice,
            self.foundation_admin,
            "Penghapusan langsung oleh yayasan",
        )
        self.assertEqual(written_off.status, InvoiceStatus.WRITTEN_OFF)
        req = InvoiceWriteOffRequest.objects.get(invoice=self.invoice)
        self.assertEqual(req.status, InvoiceWriteOffStatus.APPROVED)
        self.assertIsNotNone(req.journal)

    def test_direct_write_off_invoice_by_school_user_fails(self):
        """write_off_invoice raises PermissionDenied if caller lacks foundation authority."""
        with self.assertRaises(PermissionDenied):
            write_off_invoice(
                self.invoice,
                self.school_finance,
                "Upaya hapus buku langsung oleh sekolah",
            )

    def test_api_workflow(self):
        """Test API endpoints: create request, approve, and direct write-off."""
        # 1. School finance officer submits request via API
        self.client.force_authenticate(user=self.school_finance)
        create_resp = self.client.post('/api/v1/finance/write-offs/', {
            'invoice_id': self.invoice.id,
            'reason': "Permohonan keringanan / hapus buku",
            'amount': '800000.00',
        }, format='json')
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        request_id = create_resp.data['id']

        # 2. School finance officer tries to approve -> 403 Forbidden
        approve_attempt = self.client.post(f'/api/v1/finance/write-offs/{request_id}/approve/', {
            'notes': "Approve diri sendiri",
        }, format='json')
        self.assertEqual(approve_attempt.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Foundation Admin approves
        self.client.force_authenticate(user=self.foundation_admin)
        approve_resp = self.client.post(f'/api/v1/finance/write-offs/{request_id}/approve/', {
            'notes': "Disetujui yayasan.",
        }, format='json')
        self.assertEqual(approve_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_resp.data['status'], InvoiceWriteOffStatus.APPROVED)

        # Check invoice status
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.WRITTEN_OFF)

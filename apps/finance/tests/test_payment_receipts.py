import datetime
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import StoredFile
from apps.finance.models import (
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
)
from apps.finance.services.receipts import (
    format_idr,
    format_wib_datetime,
    generate_payment_receipt_pdf,
    get_or_create_payment_receipt,
    render_payment_receipt_html,
    terbilang,
)
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class PaymentReceiptServiceTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.patch_upload = patch('apps.core.storage.upload_bytes')
        self.mock_upload = self.patch_upload.start()
        self.addCleanup(self.patch_upload.stop)

        self.patch_download = patch('apps.core.storage.generate_download_url', return_value='https://storage.googleapis.com/test-bucket/payment_receipt.pdf')
        self.mock_download = self.patch_download.start()
        self.addCleanup(self.patch_download.stop)

        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.888.777.6-555.000",
            address="Jakarta Selatan",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Harapan Bangsa",
            npsn="20100010",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Budi Pratama",
            nik="3171010101010002",
            gender=Person.GENDER_MALE,
            dob="2015-08-12",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nis="2026001",
            nisn="0012345678",
            status=Student.STATUS_ACTIVE,
        )

        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number=f"INV/{self.school.npsn}/2026/000100",
            period="2026-10",
            due_date=datetime.date(2026, 10, 10),
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('500000.00'),
            currency='IDR',
            status=InvoiceStatus.PAID,
        )
        self.line = InvoiceLine.objects.create(
            foundation_id=self.foundation.id,
            invoice=self.invoice,
            description="SPP Oktober 2026",
            amount=Decimal('500000.00'),
            currency='IDR',
        )

        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            amount=Decimal('500000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference='PAY-TEST-001',
            external_id='EXT-TEST-001',
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
            fee=Decimal('2500.00'),
            net=Decimal('497500.00'),
        )
        self.allocation = PaymentAllocation.objects.create(
            foundation_id=self.foundation.id,
            payment=self.payment,
            invoice=self.invoice,
            invoice_line=self.line,
            amount=Decimal('500000.00'),
            currency='IDR',
        )

    def test_terbilang_conversions(self):
        self.assertEqual(terbilang(0), "Nol Rupiah")
        self.assertEqual(terbilang(1), "Satu Rupiah")
        self.assertEqual(terbilang(10), "Sepuluh Rupiah")
        self.assertEqual(terbilang(11), "Sebelas Rupiah")
        self.assertEqual(terbilang(15), "Lima Belas Rupiah")
        self.assertEqual(terbilang(100), "Seratus Rupiah")
        self.assertEqual(terbilang(150), "Seratus Lima Puluh Rupiah")
        self.assertEqual(terbilang(1000), "Seribu Rupiah")
        self.assertEqual(terbilang(1500000), "Satu Juta Lima Ratus Ribu Rupiah")
        self.assertEqual(
            terbilang(123456789),
            "Seratus Dua Puluh Tiga Juta Empat Ratus Lima Puluh Enam Ribu Tujuh Ratus Delapan Puluh Sembilan Rupiah",
        )
        self.assertEqual(terbilang(Decimal('500000.00')), "Lima Ratus Ribu Rupiah")

    def test_format_idr_and_wib_datetime(self):
        self.assertEqual(format_idr(Decimal('500000.00')), "Rp 500.000")
        self.assertEqual(format_idr(1250500), "Rp 1.250.500")

        dt = datetime.datetime(2026, 10, 15, 10, 30, tzinfo=datetime.timezone.utc)
        formatted_dt = format_wib_datetime(dt)
        self.assertIn("15 Oktober 2026", formatted_dt)
        self.assertIn("WIB", formatted_dt)

    def test_render_payment_receipt_html(self):
        with tenant_context(self.foundation.id):
            html = render_payment_receipt_html(self.payment)
            self.assertIn("KWITANSI", html)
            self.assertIn("Budi Pratama", html)
            self.assertIn("0012345678", html)
            self.assertIn("SD Harapan Bangsa", html)
            self.assertIn("Lima Ratus Ribu Rupiah", html)
            self.assertIn("Rp 500.000", html)
            self.assertIn("SPP Oktober 2026", html)

    def test_generate_and_get_payment_receipt(self):
        with tenant_context(self.foundation.id):
            receipt_info = get_or_create_payment_receipt(self.payment)
            self.assertEqual(receipt_info['payment_id'], self.payment.id)
            self.assertIsNotNone(receipt_info['receipt_number'])
            self.assertIsNotNone(receipt_info['receipt_pdf_key'])
            self.assertIsNotNone(receipt_info['download_url'])
            self.assertEqual(receipt_info['amount'], "500000.00")

            # Check StoredFile record
            sf = StoredFile.all_tenants.filter(key=receipt_info['receipt_pdf_key']).first()
            self.assertIsNotNone(sf)
            self.assertEqual(sf.purpose, 'payment_receipt')
            self.assertEqual(sf.foundation_id, self.foundation.id)

            # Idempotency check: calling again shouldn't re-create or alter receipt number
            receipt_info_2 = get_or_create_payment_receipt(self.payment)
            self.assertEqual(receipt_info_2['receipt_number'], receipt_info['receipt_number'])
            self.assertEqual(receipt_info_2['receipt_pdf_key'], receipt_info['receipt_pdf_key'])


class PaymentReceiptEndpointTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.patch_upload = patch('apps.core.storage.upload_bytes')
        self.mock_upload = self.patch_upload.start()
        self.addCleanup(self.patch_upload.stop)

        self.patch_download = patch('apps.core.storage.generate_download_url', return_value='https://storage.googleapis.com/test-bucket/payment_receipt.pdf')
        self.mock_download = self.patch_download.start()
        self.addCleanup(self.patch_download.stop)

        self.client = APIClient()

        # Foundation 1
        self.foundation1 = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.888.777.6-555.000",
            address="Jakarta Selatan",
        )
        set_current_foundation_id(self.foundation1.id)

        self.school1 = School.all_tenants.create(
            foundation_id=self.foundation1.id,
            name="SD Harapan Bangsa",
            npsn="20100010",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        # Student 1
        self.person_student1 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Ananda Budi",
            nik="3171010101010011",
            gender=Person.GENDER_MALE,
            dob="2015-01-01",
        )
        self.student1 = Student.all_tenants.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            person=self.person_student1,
            nis="2026101",
            nisn="0011223344",
            status=Student.STATUS_ACTIVE,
        )

        # Parent 1 (financial_responsible=True)
        self.user_parent1 = User.objects.create(
            foundation_id=self.foundation1.id,
            phone_e164="+6281111111111",
            email="parent1@harapan.sch.id",
            full_name="Orang Tua Budi",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation1.id,
            user=self.user_parent1,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation1.id,
        )
        self.person_parent1 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Orang Tua Budi",
            nik="3171010101010012",
            gender=Person.GENDER_MALE,
            dob="1980-01-01",
        )
        self.guardian_parent1 = Guardian.all_tenants.create(
            foundation_id=self.foundation1.id,
            person=self.person_parent1,
            user=self.user_parent1,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation1.id,
            guardian=self.guardian_parent1,
            student=self.student1,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
        )

        # Parent 2 (financial_responsible=False)
        self.user_parent2 = User.objects.create(
            foundation_id=self.foundation1.id,
            phone_e164="+6282222222222",
            email="parent2@harapan.sch.id",
            full_name="Wali Non Finansial",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation1.id,
            user=self.user_parent2,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation1.id,
        )
        self.person_parent2 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Wali Non Finansial",
            nik="3171010101010013",
            gender=Person.GENDER_FEMALE,
            dob="1982-01-01",
        )
        self.guardian_parent2 = Guardian.all_tenants.create(
            foundation_id=self.foundation1.id,
            person=self.person_parent2,
            user=self.user_parent2,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation1.id,
            guardian=self.guardian_parent2,
            student=self.student1,
            relation=GuardianLink.RELATION_MOTHER,
            financial_responsible=False,
        )

        # Student 2 & Parent 3 (Unrelated family)
        self.person_student2 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Ananda Siti",
            nik="3171010101010021",
            gender=Person.GENDER_FEMALE,
            dob="2015-05-05",
        )
        self.student2 = Student.all_tenants.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            person=self.person_student2,
            nis="2026102",
            nisn="0011223355",
            status=Student.STATUS_ACTIVE,
        )
        self.user_parent3 = User.objects.create(
            foundation_id=self.foundation1.id,
            phone_e164="+6283333333333",
            email="parent3@harapan.sch.id",
            full_name="Orang Tua Siti",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation1.id,
            user=self.user_parent3,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation1.id,
        )
        self.person_parent3 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Orang Tua Siti",
            nik="3171010101010022",
            gender=Person.GENDER_FEMALE,
            dob="1985-05-05",
        )
        self.guardian_parent3 = Guardian.all_tenants.create(
            foundation_id=self.foundation1.id,
            person=self.person_parent3,
            user=self.user_parent3,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation1.id,
            guardian=self.guardian_parent3,
            student=self.student2,
            relation=GuardianLink.RELATION_MOTHER,
            financial_responsible=True,
        )

        # Payments for Student 1
        self.payment_settled = Payment.objects.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            student=self.student1,
            amount=Decimal('450000.00'),
            currency='IDR',
            method=PaymentMethod.QRIS,
            channel='QRIS',
            reference='PAY-SETTLED-001',
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

        self.payment_pending = Payment.objects.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            student=self.student1,
            amount=Decimal('300000.00'),
            currency='IDR',
            method=PaymentMethod.VA,
            channel='BCA_VA',
            reference='PAY-PENDING-001',
            status=PaymentStatus.PENDING,
        )

        # Foundation 2 (Tenant 2)
        self.foundation2 = Foundation.objects.create(
            legal_name="Yayasan Bintang Gemilang",
            brand_name="Bintang Gemilang",
            npwp="01.999.888.7-666.000",
            address="Bandung",
        )
        self.school2 = School.all_tenants.create(
            foundation_id=self.foundation2.id,
            name="SD Bintang Gemilang",
            npsn="20200020",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )
        self.person_student_t2 = Person.all_tenants.create(
            foundation_id=self.foundation2.id,
            full_name="Siswa Tenant Dua",
            nik="3273010101010099",
            gender=Person.GENDER_MALE,
            dob="2015-09-09",
        )
        self.student_t2 = Student.all_tenants.create(
            foundation_id=self.foundation2.id,
            school=self.school2,
            person=self.person_student_t2,
            nis="2026999",
            nisn="0099887766",
            status=Student.STATUS_ACTIVE,
        )
        self.payment_tenant2 = Payment.all_tenants.create(
            foundation_id=self.foundation2.id,
            school=self.school2,
            student=self.student_t2,
            amount=Decimal('700000.00'),
            currency='IDR',
            method=PaymentMethod.CASH,
            channel='CASH_DESK',
            reference='PAY-T2-001',
            status=PaymentStatus.SETTLED,
            paid_at=timezone.now(),
            settled_at=timezone.now(),
        )

    def test_parent_access_settled_payment_receipt_success(self):
        """Financial guardian can retrieve receipt for settled payment (PAR-009, PAR-017)."""
        self.client.force_authenticate(user=self.user_parent1)
        res = self.client.get(f"/api/v1/finance/payments/{self.payment_settled.id}/receipt/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual(data['payment_id'], self.payment_settled.id)
        self.assertIsNotNone(data['download_url'])
        self.assertIsNotNone(data['receipt_number'])
        self.assertEqual(data['amount'], "450000.00")
        self.assertEqual(data['status'], PaymentStatus.SETTLED)

    def test_payment_receipt_unsettled_rejected(self):
        """Cannot generate receipt for unsettled (PENDING) payment."""
        self.client.force_authenticate(user=self.user_parent1)
        res = self.client.get(f"/api/v1/finance/payments/{self.payment_pending.id}/receipt/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", res.json())

    def test_non_financial_guardian_cannot_access_receipt(self):
        """Guardian without financial_responsible=True receives 404 (PAR-017, IAM-014)."""
        self.client.force_authenticate(user=self.user_parent2)
        res = self.client.get(f"/api/v1/finance/payments/{self.payment_settled.id}/receipt/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_family_guardian_cannot_access_receipt(self):
        """Guardian of Student 2 cannot access Student 1's payment receipt (404)."""
        self.client.force_authenticate(user=self.user_parent3)
        res = self.client.get(f"/api/v1/finance/payments/{self.payment_settled.id}/receipt/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_tenant_isolation_404(self):
        """Cross-tenant payment receipt request returns 404."""
        self.client.force_authenticate(user=self.user_parent1)
        res = self.client.get(f"/api/v1/finance/payments/{self.payment_tenant2.id}/receipt/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_payment_serializer_output_fields(self):
        """PaymentSerializer exposes student_name, receipt_download_url, and receipt_pdf_key."""
        self.client.force_authenticate(user=self.user_parent1)
        # First trigger receipt creation
        self.client.get(f"/api/v1/finance/payments/{self.payment_settled.id}/receipt/")

        res = self.client.get(f"/api/v1/finance/payments/{self.payment_settled.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual(data['student_name'], "Ananda Budi")
        self.assertIsNotNone(data['receipt_number'])
        self.assertIsNotNone(data['receipt_pdf_key'])
        self.assertIsNotNone(data['receipt_download_url'])

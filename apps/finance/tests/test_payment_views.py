import hashlib
from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PaymentViewsTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        # Tenant 1: Yayasan Surya Kencana
        self.foundation1 = Foundation.objects.create(
            legal_name="Yayasan Surya Kencana",
            brand_name="Surya Kencana",
            npwp="01.111.222.3-444.000",
            address="Semarang",
        )
        set_current_foundation_id(self.foundation1.id)

        self.school1 = School.all_tenants.create(
            foundation_id=self.foundation1.id,
            name="SD Surya Kencana",
            npsn="20300001",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )

        self.person1 = Person.all_tenants.create(
            foundation_id=self.foundation1.id,
            full_name="Citra Lestari",
            nik="3374010101010001",
            gender=Person.GENDER_FEMALE,
            dob="2016-04-15",
        )
        self.student1 = Student.all_tenants.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            person=self.person1,
            nis="202604001",
            nisn="0045678901",
            status=Student.STATUS_ACTIVE,
        )

        self.finance_user1 = User.objects.create(
            foundation_id=self.foundation1.id,
            phone_e164="+6281333333333",
            email="keuangan@suryakencana.sch.id",
            full_name="Finance Officer 1",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation1.id,
            user=self.finance_user1,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation1.id,
        )

        self.invoice1 = Invoice.objects.create(
            foundation_id=self.foundation1.id,
            school=self.school1,
            student=self.student1,
            number=f"INV/{self.school1.npsn}/2026/000201",
            period="2026-10",
            due_date="2026-10-10",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # Tenant 2: Yayasan Bintang Timur
        self.foundation2 = Foundation.objects.create(
            legal_name="Yayasan Bintang Timur",
            brand_name="Bintang Timur",
            npwp="01.222.333.4-666.000",
            address="Surabaya",
        )
        self.school2 = School.all_tenants.create(
            foundation_id=self.foundation2.id,
            name="SD Bintang Timur",
            npsn="20500099",
            level=School.LEVEL_SD,
            base_currency="IDR",
        )
        self.finance_user2 = User.objects.create(
            foundation_id=self.foundation2.id,
            phone_e164="+6281444444444",
            email="keuangan@bintangtimur.sch.id",
            full_name="Finance Officer 2",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation2.id,
            user=self.finance_user2,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation2.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_payment_intent_creation_view(self):
        """POST /api/v1/finance/payment-intents/ creates intent for invoices."""
        self.client.force_authenticate(user=self.finance_user1)
        response = self.client.post(
            '/api/v1/finance/payment-intents/',
            {
                'invoice_ids': [self.invoice1.id],
                'method': 'VA',
                'bank': 'BCA',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['amount'], '500000.00')
        self.assertEqual(response.data['currency'], 'IDR')
        self.assertIsNotNone(response.data['va_number'])

    def test_cash_payment_view(self):
        """POST /api/v1/finance/payments/cash/ records cash payment and returns receipt."""
        self.client.force_authenticate(user=self.finance_user1)
        response = self.client.post(
            '/api/v1/finance/payments/cash/',
            {
                'student_id': self.student1.id,
                'amount': '500000.00',
                'invoice_ids': [self.invoice1.id],
                'notes': 'Tunai dari wali murid',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], PaymentStatus.SETTLED)
        self.assertTrue(response.data['receipt_number'].startswith(f"RCP/{self.school1.npsn}/"))

        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.status, InvoiceStatus.PAID)

    def test_manual_payment_submission_and_verification_view(self):
        """POST /api/v1/finance/payments/manual/ then POST .../verify/."""
        self.client.force_authenticate(user=self.finance_user1)

        # Submit manual transfer
        res_submit = self.client.post(
            '/api/v1/finance/payments/manual/',
            {
                'student_id': self.student1.id,
                'amount': '500000.00',
                'invoice_ids': [self.invoice1.id],
                'proof_file': 'uploads/receipt_oct.png',
                'notes': 'Transfer ATM Bersama',
            },
            format='json',
        )
        self.assertEqual(res_submit.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_submit.data['status'], PaymentStatus.PENDING_VERIFICATION)
        payment_id = res_submit.data['id']

        # Verify transfer by finance officer
        res_verify = self.client.post(
            f'/api/v1/finance/payments/{payment_id}/verify/',
            {
                'decision': 'APPROVE',
                'reason': 'Dana sudah masuk rekening sekolah',
            },
            format='json',
        )
        self.assertEqual(res_verify.status_code, status.HTTP_200_OK)
        self.assertEqual(res_verify.data['status'], PaymentStatus.SETTLED)
        self.assertTrue(res_verify.data['receipt_number'].startswith(f"RCP/{self.school1.npsn}/"))

        self.invoice1.refresh_from_db()
        self.assertEqual(self.invoice1.status, InvoiceStatus.PAID)

    def test_public_webhook_endpoint(self):
        """POST /api/v1/finance/webhooks/payments/<provider>/ is public and processes payment."""
        order_id = "MID-WEBHOOK-VIEW-01"
        amount = "500000.00"
        secret = "mock-secret"
        sig = hashlib.sha256(f"{order_id}{amount}{secret}".encode('utf-8')).hexdigest()

        response = self.client.post(
            '/api/v1/finance/webhooks/payments/mock/',
            {
                'order_id': order_id,
                'amount': amount,
                'fee': '2500.00',
                'status': 'SETTLED',
                'channel': 'BCA_VA',
                'signature': sig,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'settled')

    def test_student_statement_view(self):
        """GET /api/v1/finance/students/<id>/statement/ returns ledger-backed student statement."""
        self.client.force_authenticate(user=self.finance_user1)
        response = self.client.get(f'/api/v1/finance/students/{self.student1.id}/statement/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['student_id'], self.student1.id)
        self.assertEqual(response.data['credit_balance']['amount'], '0.00')
        self.assertEqual(len(response.data['invoices']), 1)

    def test_cross_tenant_isolation_404(self):
        """Layer 3 Tenancy: User from Tenant 2 cannot access Tenant 1 student statement or payments."""
        self.client.force_authenticate(user=self.finance_user2)
        response = self.client.get(f'/api/v1/finance/students/{self.student1.id}/statement/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

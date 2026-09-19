from decimal import Decimal
import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.finance.models import (
    FeeCategory,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
)
from educore.middleware.tenancy import set_current_foundation_id, clear_current_foundation_id, tenant_context


class InvoiceViewsTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Tunas Bangsa",
            brand_name="Tunas Bangsa",
            npwp="01.555.444.3-222.000",
            address="Surabaya",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Tunas Bangsa",
            npsn="20500001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281999999999",
            email="bendahara@tunasbangsa.sch.id",
            full_name="Bendahara Tunas Bangsa",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.admin_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281888888888",
            email="admin@tunasbangsa.sch.id",
            full_name="Admin Yayasan Tunas Bangsa",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.admin_user,
            role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.fee = FeeType.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            code="SPP_REG",
            name="SPP Reguler",
            category=FeeCategory.SPP,
            recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('750000.00'),
            currency='IDR',
        )

        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3578010101010001",
            full_name="Bambang Pamungkas",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person,
            nisn="1234567890",
            nis="SMA-TB-01",
            status=Student.STATUS_ACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_api_generate_action_dry_run_and_commit(self):
        """Verify POST /api/v1/finance/invoices/generate/ produces dry_run and commit batches."""
        self.client.force_authenticate(user=self.finance_user)

        # 1. Dry run
        dry_payload = {
            'school_id': self.school.id,
            'period': '2026-10',
            'dry_run': True,
        }
        res = self.client.post('/api/v1/finance/invoices/generate/', dry_payload, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['dry_run'])
        self.assertEqual(res.json()['generated_count'], 1)
        self.assertEqual(Invoice.objects.filter(period='2026-10').count(), 0)

        # 2. Commit
        commit_payload = {
            'school_id': self.school.id,
            'period': '2026-10',
            'dry_run': False,
        }
        res_commit = self.client.post('/api/v1/finance/invoices/generate/', commit_payload, format='json')
        self.assertEqual(res_commit.status_code, 200)
        self.assertFalse(res_commit.json()['dry_run'])
        self.assertEqual(res_commit.json()['generated_count'], 1)

        # Invoice persisted with formatted 2dp string amounts (CUR-026)
        with tenant_context(self.foundation.id):
            inv = Invoice.objects.filter(period='2026-10').first()
            self.assertIsNotNone(inv)
            self.assertEqual(inv.total, Decimal('750000.00'))

    def test_api_list_and_overdue_filter(self):
        """Verify GET /api/v1/finance/invoices/ with overdue and period filters."""
        self.client.force_authenticate(user=self.finance_user)

        # Create past overdue invoice and future invoice
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20500001/2026/000001",
            period="2026-08",
            issue_date=datetime.date(2026, 8, 1),
            due_date=datetime.date(2026, 8, 10),  # In the past
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        res = self.client.get('/api/v1/finance/invoices/?overdue=true')
        self.assertEqual(res.status_code, 200)
        results = res.json().get('results', res.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['period'], '2026-08')
        self.assertEqual(results[0]['total'], '750000.00')
        self.assertTrue(results[0]['is_overdue'])

    def test_api_cancel_and_write_off(self):
        """Verify POST /cancel/ and POST /write-off/ actions."""
        self.client.force_authenticate(user=self.finance_user)

        inv = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20500001/2026/000002",
            period="2026-09",
            due_date=datetime.date(2026, 9, 10),
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

        # Cancel
        res_cancel = self.client.post(f'/api/v1/finance/invoices/{inv.id}/cancel/', {'reason': 'Salah terbit'})
        self.assertEqual(res_cancel.status_code, 200)
        self.assertEqual(res_cancel.json()['status'], InvoiceStatus.CANCELLED)

        # Write-off test on new invoice
        inv2 = Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/20500001/2026/000003",
            period="2026-07",
            due_date=datetime.date(2026, 7, 10),
            total=Decimal('750000.00'),
            currency='IDR',
            status=InvoiceStatus.ISSUED,
        )
        # Write-off approval requires Foundation Admin authority (FIN-031).
        self.client.force_authenticate(user=self.admin_user)
        res_wo = self.client.post(f'/api/v1/finance/invoices/{inv2.id}/write-off/', {'reason': 'Piutang tak tertagih'})
        self.assertEqual(res_wo.status_code, 200)
        self.assertEqual(res_wo.json()['status'], InvoiceStatus.WRITTEN_OFF)

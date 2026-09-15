from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.finance.models import Invoice, InvoiceLine, InvoiceStatus
from apps.finance.services.ar_aging import (
    AGING_BUCKETS,
    get_aging_bucket,
    get_ar_aging_report,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ArAgingUnitTests(TestCase):
    def test_get_aging_bucket_boundaries(self):
        """Test standard AR aging day bucket assignments (FIN-029)."""
        as_of = date(2025, 6, 1)

        # Future due date -> CURRENT
        self.assertEqual(get_aging_bucket(date(2025, 6, 2), as_of), 'CURRENT')
        self.assertEqual(get_aging_bucket(date(2025, 7, 1), as_of), 'CURRENT')

        # 0 to 30 days overdue -> 0_30
        self.assertEqual(get_aging_bucket(date(2025, 6, 1), as_of), '0_30')  # 0 days
        self.assertEqual(get_aging_bucket(date(2025, 5, 20), as_of), '0_30')  # 12 days
        self.assertEqual(get_aging_bucket(date(2025, 5, 2), as_of), '0_30')  # 30 days

        # 31 to 60 days overdue -> 31_60
        self.assertEqual(get_aging_bucket(date(2025, 5, 1), as_of), '31_60')  # 31 days
        self.assertEqual(get_aging_bucket(date(2025, 4, 15), as_of), '31_60')  # 47 days
        self.assertEqual(get_aging_bucket(date(2025, 4, 2), as_of), '31_60')  # 60 days

        # 61 to 90 days overdue -> 61_90
        self.assertEqual(get_aging_bucket(date(2025, 4, 1), as_of), '61_90')  # 61 days
        self.assertEqual(get_aging_bucket(date(2025, 3, 15), as_of), '61_90')  # 78 days
        self.assertEqual(get_aging_bucket(date(2025, 3, 3), as_of), '61_90')  # 90 days

        # 91+ days overdue -> 90_PLUS
        self.assertEqual(get_aging_bucket(date(2025, 3, 2), as_of), '90_PLUS')  # 91 days
        self.assertEqual(get_aging_bucket(date(2025, 1, 1), as_of), '90_PLUS')  # 151 days


class ArAgingReportAndApiTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            npwp="01.123.456.7-890.000",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)

        self.school_sma = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="20100001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )
        self.school_smp = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Harapan Bangsa",
            npsn="20100002",
            level=School.LEVEL_SMP,
            base_currency="IDR",
        )

        # Academic Year & Class Groups
        self.ay = AcademicYear.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_sma,
            name="2024/2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            is_active=True,
        )
        self.class_x = ClassGroup.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_sma,
            academic_year=self.ay,
            grade_level=10,
            name="X-A",
        )
        self.class_xi = ClassGroup.objects.create(
            foundation_id=self.foundation.id,
            school=self.school_sma,
            academic_year=self.ay,
            grade_level=11,
            name="XI-B",
        )

        # Users & Students
        # Student 1 in Class X
        person_1 = Person.objects.create(foundation_id=self.foundation.id, full_name="Siswa Satu", nik="3171010000000001")
        self.student_1 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_sma, person=person_1, nis="NIS001", nisn="0011111111", status=Student.STATUS_ACTIVE
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student_1, class_group=self.class_x, enrolled_at=date(2024, 7, 1), is_active=True
        )

        # Student 2 in Class XI
        person_2 = Person.objects.create(foundation_id=self.foundation.id, full_name="Siswa Dua", nik="3171010000000002")
        self.student_2 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_sma, person=person_2, nis="NIS002", nisn="0022222222", status=Student.STATUS_ACTIVE
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student_2, class_group=self.class_xi, enrolled_at=date(2024, 7, 1), is_active=True
        )

        # Student 3 in School SMP (no class enrollment)
        person_3 = Person.objects.create(foundation_id=self.foundation.id, full_name="Siswa Tiga", nik="3171010000000003")
        self.student_3 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_smp, person=person_3, nis="NIS003", nisn="0033333333", status=Student.STATUS_ACTIVE
        )

        # User for API auth
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="finance@yayasan.org",
            full_name="Staff Keuangan",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # Reference as_of date: 2025-06-01
        self.as_of = date(2025, 6, 1)

        # Create Invoices with specific due dates relative to as_of
        # 1. Student 1: Future invoice -> CURRENT (due 2025-06-10, total 1,000,000, paid 0)
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_1,
            number="INV/SMA/2025/0001", period="2025-06", due_date=date(2025, 6, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
        )
        # 2. Student 1: Overdue 10 days -> 0_30 (due 2025-05-22, total 1,000,000, paid 200,000, balance 800,000)
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_1,
            number="INV/SMA/2025/0002", period="2025-05", due_date=date(2025, 5, 22),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), paid=Decimal('200000.00'),
            status=InvoiceStatus.PARTIALLY_PAID,
        )
        # 3. Student 2: Overdue 40 days -> 31_60 (due 2025-04-22, total 1,500,000, paid 0)
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_2,
            number="INV/SMA/2025/0003", period="2025-04", due_date=date(2025, 4, 22),
            subtotal=Decimal('1500000.00'), total=Decimal('1500000.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
        )
        # 4. Student 3: Overdue 100 days -> 90_PLUS (due 2025-02-21, total 500,000, paid 0) in SMP
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_smp, student=self.student_3,
            number="INV/SMP/2025/0001", period="2025-02", due_date=date(2025, 2, 21),
            subtotal=Decimal('500000.00'), total=Decimal('500000.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
        )
        # 5. Student 1: Paid invoice (due 2025-01-10) -> MUST BE EXCLUDED
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_1,
            number="INV/SMA/2025/0004", period="2025-01", due_date=date(2025, 1, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), paid=Decimal('1000000.00'),
            status=InvoiceStatus.PAID,
        )
        # 6. Student 1: Cancelled invoice -> MUST BE EXCLUDED
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_1,
            number="INV/SMA/2025/0005", period="2024-12", due_date=date(2024, 12, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.CANCELLED,
        )
        # 7. Student 1: Written off invoice -> MUST BE EXCLUDED
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school_sma, student=self.student_1,
            number="INV/SMA/2025/0006", period="2024-11", due_date=date(2024, 11, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.WRITTEN_OFF,
        )

        # Foundation 2 (for multi-tenant isolation tests)
        self.foundation_2 = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Yayasan Lain",
            npwp="02.222.222.2-222.000",
            address="Bandung",
        )
        self.school_other = School.all_tenants.create(
            foundation_id=self.foundation_2.id,
            name="SMA Lain",
            npsn="20200001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )
        person_other = Person.objects.create(foundation_id=self.foundation_2.id, full_name="Siswa Luar", nik="3271010000000001")
        student_other = Student.all_tenants.create(
            foundation_id=self.foundation_2.id, school=self.school_other, person=person_other, nis="NIS999", nisn="0099999999", status=Student.STATUS_ACTIVE
        )
        Invoice.objects.create(
            foundation_id=self.foundation_2.id, school=self.school_other, student=student_other,
            number="INV/LAIN/2025/0001", period="2025-05", due_date=date(2025, 5, 20),
            subtotal=Decimal('9999999.00'), total=Decimal('9999999.00'), paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_report_service_calculation(self):
        """Service returns accurate bucket totals, summary, and rollups."""
        report = get_ar_aging_report(
            foundation_id=self.foundation.id,
            as_of=self.as_of,
        )

        summary = report['summary']
        # Total expected:
        # CURRENT: 1,000,000 (Student 1)
        # 0_30: 800,000 (Student 1 remaining balance)
        # 31_60: 1,500,000 (Student 2)
        # 61_90: 0.00
        # 90_PLUS: 500,000 (Student 3)
        # Total Outstanding: 3,800,000
        self.assertEqual(summary['CURRENT'], '1000000.00')
        self.assertEqual(summary['0_30'], '800000.00')
        self.assertEqual(summary['31_60'], '1500000.00')
        self.assertEqual(summary['61_90'], '0.00')
        self.assertEqual(summary['90_PLUS'], '500000.00')
        self.assertEqual(summary['total_outstanding'], '3800000.00')
        self.assertEqual(summary['invoices_count'], 4)

        # Rollup by school
        by_school = report['by_school']
        self.assertEqual(len(by_school), 2)
        sma_entry = next(s for s in by_school if s['school_id'] == self.school_sma.id)
        smp_entry = next(s for s in by_school if s['school_id'] == self.school_smp.id)

        self.assertEqual(sma_entry['total_outstanding'], '3300000.00')
        self.assertEqual(smp_entry['total_outstanding'], '500000.00')

        # Rollup by class group (in SMA)
        by_class = report['by_class_group']
        class_x_entry = next(c for c in by_class if c['class_group_id'] == self.class_x.id)
        class_xi_entry = next(c for c in by_class if c['class_group_id'] == self.class_xi.id)

        # Class X has Student 1: 1,000,000 (CURRENT) + 800,000 (0_30) = 1,800,000
        self.assertEqual(class_x_entry['total_outstanding'], '1800000.00')
        # Class XI has Student 2: 1,500,000 (31_60)
        self.assertEqual(class_xi_entry['total_outstanding'], '1500000.00')

        # Rollup by student
        by_student = report['by_student']
        s1 = next(s for s in by_student if s['student_id'] == self.student_1.id)
        self.assertEqual(s1['CURRENT'], '1000000.00')
        self.assertEqual(s1['0_30'], '800000.00')
        self.assertEqual(s1['total_outstanding'], '1800000.00')
        self.assertEqual(s1['invoices_count'], 2)

    def test_report_service_school_filter(self):
        """Filtering by school_id restricts summary and rollups to that school."""
        report = get_ar_aging_report(
            foundation_id=self.foundation.id,
            as_of=self.as_of,
            school=self.school_smp,
        )
        self.assertEqual(report['summary']['total_outstanding'], '500000.00')
        self.assertEqual(len(report['by_school']), 1)
        self.assertEqual(report['by_school'][0]['school_id'], self.school_smp.id)

    def test_api_endpoint_success(self):
        """GET /api/v1/finance/ar-aging/ returns 200 OK with expected json structure."""
        self.client.force_authenticate(user=self.finance_user)
        response = self.client.get('/api/v1/finance/ar-aging/', {
            'as_of': '2025-06-01',
            'school_id': self.school_sma.id,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data

        self.assertEqual(data['as_of'], '2025-06-01')
        self.assertEqual(data['currency'], 'IDR')
        self.assertIn('buckets', data)
        self.assertIn('summary', data)
        self.assertIn('by_school', data)
        self.assertIn('by_class_group', data)
        self.assertIn('by_student', data)
        self.assertEqual(data['summary']['total_outstanding'], '3300000.00')

    def test_api_tenancy_isolation(self):
        """Foundation 1 user cannot see invoices or AR aging of Foundation 2."""
        self.client.force_authenticate(user=self.finance_user)
        response = self.client.get('/api/v1/finance/ar-aging/', {'as_of': '2025-06-01'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 9,999,999 from Foundation 2 must NOT be included
        data = response.data
        self.assertEqual(data['summary']['total_outstanding'], '3800000.00')
        student_ids = [s['student_id'] for s in data['by_student']]
        self.assertNotIn(self.school_other.id, [s['school_id'] for s in data['by_school']])

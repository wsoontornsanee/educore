import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Person, School, Student
from apps.reporting.models import RptArAging
from apps.reporting.services import refresh_ar_aging
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class RefreshArAgingTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Reporting AR",
            brand_name="Yayasan Reporting AR",
            npwp="01.888.777.6-555.000",
            address="Semarang",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Reporting AR",
            npsn="30200001",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )

        # Student 1
        person_1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3374010100000001",
            full_name="Siswa AR Satu",
        )
        self.student_1 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=person_1,
            nis="AR-001",
            nisn="0011223344",
            status=Student.STATUS_ACTIVE,
        )

        # Student 2
        person_2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3374010100000002",
            full_name="Siswa AR Dua",
        )
        self.student_2 = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=person_2,
            nis="AR-002",
            nisn="0022334455",
            status=Student.STATUS_ACTIVE,
        )

        self.today = timezone.now().date()

        # Invoices for Student 1:
        # Invoice 1: Overdue 10 days -> 0_30 bucket (due today - 10)
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_1,
            number="INV/AR/001",
            period="2025-05",
            due_date=self.today - datetime.timedelta(days=10),
            subtotal=Decimal('1000000.00'),
            total=Decimal('1000000.00'),
            paid=Decimal('200000.00'),  # balance 800,000
            status=InvoiceStatus.PARTIALLY_PAID,
            currency='IDR',
        )
        # Invoice 2: Overdue 40 days -> 31_60 bucket (due today - 40)
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_1,
            number="INV/AR/002",
            period="2025-04",
            due_date=self.today - datetime.timedelta(days=40),
            subtotal=Decimal('1200000.00'),
            total=Decimal('1200000.00'),
            paid=Decimal('0.00'),  # balance 1,200,000
            status=InvoiceStatus.ISSUED,
            currency='IDR',
        )

        # Invoices for Student 2:
        # Invoice 3: Overdue 95 days -> 90_PLUS bucket
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_2,
            number="INV/AR/003",
            period="2025-02",
            due_date=self.today - datetime.timedelta(days=95),
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            status=InvoiceStatus.ISSUED,
            currency='IDR',
        )
        # Invoice 4: Paid invoice -> EXCLUDED
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_2,
            number="INV/AR/004",
            period="2025-01",
            due_date=self.today - datetime.timedelta(days=120),
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('500000.00'),
            status=InvoiceStatus.PAID,
            currency='IDR',
        )
        # Invoice 5: Written-off invoice -> EXCLUDED
        Invoice.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_2,
            number="INV/AR/005",
            period="2024-12",
            due_date=self.today - datetime.timedelta(days=150),
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            status=InvoiceStatus.WRITTEN_OFF,
            currency='IDR',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_refresh_ar_aging_service(self):
        """refresh_ar_aging creates rows in RptArAging matching open invoice buckets."""
        result = refresh_ar_aging(scope='full')
        self.assertGreaterEqual(result['rows_written'], 3)

        # Student 1 in 0_30
        row_s1_0_30 = RptArAging.all_tenants.get(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_1,
            bucket='0_30',
            as_of=self.today,
        )
        self.assertEqual(row_s1_0_30.amount, Decimal('800000.00'))
        self.assertEqual(row_s1_0_30.invoices_count, 1)

        # Student 1 in 31_60
        row_s1_31_60 = RptArAging.all_tenants.get(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_1,
            bucket='31_60',
            as_of=self.today,
        )
        self.assertEqual(row_s1_31_60.amount, Decimal('1200000.00'))
        self.assertEqual(row_s1_31_60.invoices_count, 1)

        # Student 2 in 90_PLUS
        row_s2_90_plus = RptArAging.all_tenants.get(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student_2,
            bucket='90_PLUS',
            as_of=self.today,
        )
        self.assertEqual(row_s2_90_plus.amount, Decimal('500000.00'))
        self.assertEqual(row_s2_90_plus.invoices_count, 1)

        # Paid and written-off invoices should not create additional rows
        total_rows = RptArAging.all_tenants.filter(foundation_id=self.foundation.id, as_of=self.today).count()
        self.assertEqual(total_rows, 3)

    def test_refresh_reporting_command_with_rpt_ar_aging(self):
        """Management command refresh_reporting runs smoothly and includes rpt_ar_aging."""
        call_command('refresh_reporting', scope='full')
        rows = RptArAging.all_tenants.filter(foundation_id=self.foundation.id, as_of=self.today)
        self.assertEqual(rows.count(), 3)

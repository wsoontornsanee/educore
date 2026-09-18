"""DSAR access export (CMP-011), reusing the CMP-016 PII-export ExportJob pipeline."""
import datetime
from decimal import Decimal

from django.test import TestCase

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.compliance.exports import REPORT_KEY_DSAR_ACCESS
from apps.compliance.services import PersonNotFoundError, collect_person_data_bundle
from apps.core.models import ExportJob
from apps.core.services import get_export_allowed_formats, get_export_renderer, is_export_pii
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class CollectPersonDataBundleTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-002.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji", npsn="40100099", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Siti Aminah",
            nik="3171010101019999", dob=datetime.date(2009, 1, 1),
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026099", nisn="0099999999", status=Student.STATUS_ACTIVE,
        )
        self.academic_year = AcademicYear.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            name="2026/2027", start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30),
        )
        self.rombel = ClassGroup.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            academic_year=self.academic_year, grade_level=10, name="X IPA 1",
        )
        ClassEnrollment.all_tenants.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.rombel, enrolled_at=datetime.date(2026, 7, 15), is_active=True,
        )
        AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=datetime.date(2026, 8, 3), status=AttendanceStatus.HADIR,
        )
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number="INV/UJI/2026/000099", period="2026-08", due_date=datetime.date(2026, 8, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_bundle_contains_all_four_domains(self):
        bundle = collect_person_data_bundle('STUDENT', self.student.id, self.foundation.id)
        self.assertEqual(bundle['identity']['full_name'], 'Siti Aminah')
        self.assertEqual(bundle['identity']['nik'], '3171010101019999')
        self.assertEqual(len(bundle['academic_enrollments']), 1)
        self.assertEqual(bundle['academic_enrollments'][0]['class_group'], 'X IPA 1')
        self.assertEqual(len(bundle['attendance_days']), 1)
        self.assertEqual(bundle['attendance_days'][0]['status'], 'HADIR')
        self.assertEqual(len(bundle['invoices']), 1)
        self.assertEqual(bundle['invoices'][0]['number'], 'INV/UJI/2026/000099')

    def test_unknown_student_raises(self):
        with self.assertRaises(PersonNotFoundError):
            collect_person_data_bundle('STUDENT', 999999, self.foundation.id)


class DsarAccessExportRegistrationTests(TestCase):
    """CMP-016 wiring: dsar_access must be PII-registered so watermark +
    PiiExportAccessLog fire automatically via run_export_job (unchanged from
    the DAPODIK/EMIS exporter's own registration)."""

    def test_renderer_registered(self):
        self.assertIsNotNone(get_export_renderer(REPORT_KEY_DSAR_ACCESS))

    def test_registered_as_pii(self):
        self.assertTrue(is_export_pii(REPORT_KEY_DSAR_ACCESS))

    def test_allowed_formats_xlsx_only(self):
        self.assertEqual(get_export_allowed_formats(REPORT_KEY_DSAR_ACCESS), {ExportJob.FORMAT_XLSX})


class DsarAccessExportRendererTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Render", brand_name="Uji Render", npwp="01.000.000.0-007.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Render", npsn="40100093", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Rendi Saputra", nik="3171010101014444",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026093", status=Student.STATUS_ACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_renderer_produces_xlsx_bytes(self):
        renderer = get_export_renderer(REPORT_KEY_DSAR_ACCESS)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DSAR_ACCESS,
            format=ExportJob.FORMAT_XLSX,
            filters={'subject_type': 'STUDENT', 'subject_id': self.student.id},
        )
        data, content_type, filename = renderer(job)
        self.assertTrue(len(data) > 0)
        self.assertIn('spreadsheetml', content_type)
        self.assertTrue(filename.endswith('.xlsx'))

    def test_renderer_unknown_subject_raises(self):
        from apps.compliance.services import PersonNotFoundError

        renderer = get_export_renderer(REPORT_KEY_DSAR_ACCESS)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DSAR_ACCESS,
            format=ExportJob.FORMAT_XLSX,
            filters={'subject_type': 'STUDENT', 'subject_id': 999999},
        )
        with self.assertRaises(PersonNotFoundError):
            renderer(job)

"""Tests for DAPODIK/EMIS statutory export tooling (spec/14 §4, CMP-017..CMP-022).

Covers:
- Pre-export validation report (CMP-018): missing NISN/NIK/NUPTK, format
  violations, students without rombel — by name and class (spec/14 §7 #1)
- Exporter bundle build driven by schema field_map (CMP-020)
- Adapter interface (CMP-021): DapodikFileExporter / EmisFileExporter swap
- Validation endpoint permissions and tenant scoping
- XLSX/CSV renderers registered on the ExportJob pipeline
"""
from decimal import Decimal
from datetime import date

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.compliance.exports import REPORT_KEY_DAPODIK, REPORT_KEY_EMIS
from apps.compliance.models import StatutoryExportSchema, StatutorySystem
from apps.compliance.services import (
    DapodikFileExporter,
    EmisFileExporter,
    StatutoryExportError,
    get_active_schema,
    get_exporter,
    validate_statutory_export,
)
from apps.core.models import ExportJob
from apps.core.services import get_export_allowed_formats, get_export_renderer
from apps.identity.models import Foundation, Person, School, Staff, Student, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class StatutoryExportFixtures(TestCase):
    """Shared fixture builder for validation/export unit tests."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa", brand_name="Bina Bangsa", npwp="01.888.777.6-001.000",
        )
        set_ctx = self.foundation.id
        from educore.middleware.tenancy import set_current_foundation_id
        set_current_foundation_id(set_ctx)

        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Bina Bangsa",
            npsn="40100001", level=School.LEVEL_SMA,
        )
        self.madrasah = School.all_tenants.create(
            foundation_id=self.foundation.id, name="MA Bina Bangsa",
            npsn="40100002", level=School.LEVEL_MA,
        )

        # Homeroom teacher with complete data
        self.teacher_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Pak Guru Wali",
            nik="3171010101010001", gender=Person.GENDER_MALE, dob=date(1985, 5, 20),
        )
        self.teacher_user = User.all_tenants.create_user(
            phone_e164="+6281888888881", foundation_id=self.foundation.id,
            full_name="Pak Guru Wali",
        )
        self.teacher = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=self.teacher_person, user=self.teacher_user,
            school=self.school, nuptk="1612345678900001", nip="198505202010011001",
            employment_type=Staff.TYPE_PERMANENT, join_date=date(2010, 1, 1),
        )

        # Student with complete data, enrolled in a rombel
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Andi Wijaya",
            nik="3171010101010002", gender=Person.GENDER_MALE, dob=date(2008, 3, 15),
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.student_person,
            nis="2026001", nisn="0061234567", status=Student.STATUS_ACTIVE,
        )

        self.academic_year = AcademicYear.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            name="2026/2027", start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )
        self.rombel = ClassGroup.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            academic_year=self.academic_year, grade_level=10, name="X IPA 1",
            homeroom_teacher=self.teacher,
        )
        ClassEnrollment.all_tenants.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.rombel, enrolled_at=date(2026, 7, 15), is_active=True,
        )

    def tearDown(self):
        clear_current_foundation_id()


class StatutoryValidationServiceTests(StatutoryExportFixtures):
    def test_complete_data_validates_clean(self):
        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        total = sum(len(v) for v in issues.values())
        self.assertEqual(total, 0, f"Expected no issues, got: {issues}")

    def test_missing_nisn_reported_with_name_and_rombel(self):
        """spec/14 §7 criterion 1: dry-run lists every student missing a
        NISN, by name and class."""
        self.student.nisn = None
        self.student.save(update_fields=['nisn'])

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        student_issues = issues['students']
        self.assertEqual(len(student_issues), 1)
        self.assertEqual(student_issues[0]['record'], 'Andi Wijaya')
        self.assertEqual(student_issues[0]['rombel'], 'X IPA 1')
        self.assertEqual(student_issues[0]['field'], 'nisn')
        self.assertIn('NISN kosong', student_issues[0]['problem'])

    def test_malformed_nisn_reported(self):
        self.student.nisn = '12345'  # 5 digits, not 10
        self.student.save(update_fields=['nisn'])

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        nisn_issues = [i for i in issues['students'] if i['field'] == 'nisn']
        self.assertEqual(len(nisn_issues), 1)
        self.assertIn('10 digit', nisn_issues[0]['problem'])

    def test_missing_nik_reported(self):
        self.student_person.nik = None
        self.student_person.save(update_fields=['nik'])

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        nik_issues = [i for i in issues['students'] if i['field'] == 'person.nik']
        self.assertEqual(len(nik_issues), 1)
        self.assertIn('NIK kosong', nik_issues[0]['problem'])

    def test_active_student_without_rombel_reported(self):
        ClassEnrollment.all_tenants.filter(student=self.student).update(is_active=False)

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        rombel_issues = [i for i in issues['students'] if i['field'] == 'rombel']
        self.assertEqual(len(rombel_issues), 1)
        self.assertIn('tanpa rombel', rombel_issues[0]['problem'])

    def test_staff_missing_nuptk_reported(self):
        self.teacher.nuptk = None
        self.teacher.save(update_fields=['nuptk'])

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        nuptk_issues = [i for i in issues['staff'] if i['field'] == 'nuptk']
        self.assertEqual(len(nuptk_issues), 1)
        self.assertEqual(nuptk_issues[0]['record'], 'Pak Guru Wali')

    def test_school_missing_npsn_reported(self):
        school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Tanpa NPSN",
            npsn="", level=School.LEVEL_SMA,
        )
        issues = validate_statutory_export(school, StatutorySystem.DAPODIK)
        self.assertEqual(len(issues['school']), 1)
        self.assertEqual(issues['school'][0]['field'], 'npsn')

    def test_rombel_without_homeroom_teacher_reported(self):
        orphan_rombel = ClassGroup.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            academic_year=self.academic_year, grade_level=11, name="XI IPA 1",
            homeroom_teacher=None,
        )
        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        rombel_issues = [i for i in issues['rombel'] if i['record'] == 'XI IPA 1']
        self.assertEqual(len(rombel_issues), 1)
        self.assertIn('wali kelas', rombel_issues[0]['problem'])
        ClassGroup.all_tenants.filter(id=orphan_rombel.id).delete()

    def test_cross_school_data_not_leaked(self):
        """Validation for SMA must not flag issues belonging to MA."""
        # Give the madrasah a student with missing NISN
        madrasah_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Budi Madrasah",
            nik="3171010101010009", gender=Person.GENDER_MALE, dob=date(2009, 1, 1),
        )
        madrasah_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.madrasah, person=madrasah_person,
            nis="2026009", nisn=None, status=Student.STATUS_ACTIVE,
        )

        issues_sma = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        sma_records = {i['record'] for i in issues_sma['students']}
        self.assertNotIn('Budi Madrasah', sma_records)

        issues_ma = validate_statutory_export(self.madrasah, StatutorySystem.DAPODIK)
        ma_records = {i['record'] for i in issues_ma['students']}
        self.assertIn('Budi Madrasah', ma_records)
        self.assertNotIn('Andi Wijaya', ma_records)


class StatutorySchemaTests(StatutoryExportFixtures):
    def test_active_schema_resolution(self):
        schema = StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK, version='2026.1', is_active=True,
        )
        StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK, version='2026.2', is_active=False,
        )
        resolved = get_active_schema(self.foundation.id, StatutorySystem.DAPODIK)
        self.assertEqual(resolved.id, schema.id)

    def test_no_schema_returns_none(self):
        self.assertIsNone(get_active_schema(self.foundation.id, StatutorySystem.DAPODIK))

    def test_custom_field_map_drives_export_columns(self):
        """CMP-020: schema is data — a custom field_map renames columns
        without any code change."""
        custom_map = {
            'students': {'person.full_name': 'NAMA_PESERTA', 'nisn': 'NISN'},
        }
        StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK, version='custom', is_active=True,
            field_map=custom_map,
        )

        exporter = get_exporter(StatutorySystem.DAPODIK, self.school)
        bundle = exporter.build_bundle()
        students = bundle['sheets']['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(set(students[0].keys()), {'NAMA_PESERTA', 'NISN'})
        self.assertEqual(students[0]['NAMA_PESERTA'], 'Andi Wijaya')


class StatutoryExporterTests(StatutoryExportFixtures):
    def test_dapodik_bundle_contains_all_sheets(self):
        exporter = DapodikFileExporter(school=self.school)
        bundle = exporter.build_bundle()

        self.assertEqual(bundle['metadata']['system'], StatutorySystem.DAPODIK)
        self.assertEqual(bundle['metadata']['school'], 'SMA Bina Bangsa')

        students = bundle['sheets']['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['nama'], 'Andi Wijaya')
        self.assertEqual(students[0]['nisn'], '0061234567')
        self.assertEqual(students[0]['nik'], '3171010101010002')
        self.assertEqual(students[0]['rombel'], 'X IPA 1')

        staff = bundle['sheets']['staff']
        self.assertEqual(len(staff), 1)
        self.assertEqual(staff[0]['nama'], 'Pak Guru Wali')
        self.assertEqual(staff[0]['nuptk'], '1612345678900001')

        rombels = bundle['sheets']['rombel']
        self.assertEqual(len(rombels), 1)
        self.assertEqual(rombels[0]['nama_rombel'], 'X IPA 1')
        self.assertEqual(rombels[0]['wali_kelas'], 'Pak Guru Wali')

    def test_emis_exporter_swaps_adapter(self):
        """CMP-019/CMP-021: madrasah uses the EMIS path via the same
        interface."""
        exporter = EmisFileExporter(school=self.madrasah)
        bundle = exporter.build_bundle()
        self.assertEqual(bundle['metadata']['system'], StatutorySystem.EMIS)

    def test_get_exporter_unknown_system_raises(self):
        with self.assertRaises(StatutoryExportError):
            get_exporter('UNKNOWN', self.school)

    def test_validate_delegates_to_service(self):
        exporter = DapodikFileExporter(school=self.school)
        self.student.nisn = None
        self.student.save(update_fields=['nisn'])
        issues = exporter.validate()
        self.assertGreater(len(issues['students']), 0)


class StatutoryValidationEndpointTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa", brand_name="Bina Bangsa", npwp="01.888.777.6-001.000",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="02.111.222.3-001.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Bina Bangsa",
            npsn="40100001", level=School.LEVEL_SMA,
        )
        self.other_foundation_school = School.all_tenants.create(
            foundation_id=self.other_foundation.id, name="SMA Yayasan Lain",
            npsn="40200001", level=School.LEVEL_SMA,
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999999", foundation_id=self.foundation.id, full_name="Ketua Yayasan",
        )
        assign_role(
            user=self.admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )
        self.teacher = User.all_tenants.create_user(
            phone_e164="+6281999999998", foundation_id=self.foundation.id, full_name="Guru",
        )
        assign_role(
            user=self.teacher, role=ROLE_TEACHER, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_admin_gets_validation_report(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get(
                f'/api/v1/foundation/statutory-validation?school_id={self.school.id}&system=DAPODIK'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['school_id'], self.school.id)
        self.assertEqual(data['system'], 'DAPODIK')
        self.assertIn('issues', data)
        self.assertIn('is_valid', data)

    def test_teacher_forbidden(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.get(
                f'/api/v1/foundation/statutory-validation?school_id={self.school.id}'
            )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_tenant_school_returns_404(self):
        """Layer 3 tenancy: another foundation's school is invisible."""
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get(
                f'/api/v1/foundation/statutory-validation?school_id={self.other_foundation_school.id}'
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_system_rejected(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get(
                f'/api/v1/foundation/statutory-validation?school_id={self.school.id}&system=FOO'
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class StatutoryExportRendererRegistrationTests(StatutoryExportFixtures):
    def test_dapodik_renderer_registered(self):
        self.assertIsNotNone(get_export_renderer(REPORT_KEY_DAPODIK))
        self.assertIsNotNone(get_export_renderer(REPORT_KEY_EMIS))

    def test_allowed_formats(self):
        self.assertEqual(
            get_export_allowed_formats(REPORT_KEY_DAPODIK),
            {ExportJob.FORMAT_XLSX, ExportJob.FORMAT_CSV},
        )
        self.assertEqual(
            get_export_allowed_formats(REPORT_KEY_EMIS),
            {ExportJob.FORMAT_XLSX, ExportJob.FORMAT_CSV},
        )

    def test_xlsx_renderer_produces_bytes(self):
        renderer = get_export_renderer(REPORT_KEY_DAPODIK)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DAPODIK,
            format=ExportJob.FORMAT_XLSX,
            filters={'school_id': self.school.id, 'system': StatutorySystem.DAPODIK},
        )
        data, content_type, filename = renderer(job)
        self.assertTrue(len(data) > 0)
        self.assertIn('spreadsheetml', content_type)
        self.assertTrue(filename.endswith('.xlsx'))

    def test_csv_renderer_produces_students_csv(self):
        renderer = get_export_renderer(REPORT_KEY_DAPODIK)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DAPODIK,
            format=ExportJob.FORMAT_CSV,
            filters={'school_id': self.school.id, 'system': StatutorySystem.DAPODIK, 'sheet': 'students'},
        )
        data, content_type, filename = renderer(job)
        text = data.decode('utf-8-sig')
        self.assertIn('nisn', text)
        self.assertIn('Andi Wijaya', text)
        self.assertTrue(filename.endswith('.csv'))

    def test_renderer_unknown_school_raises(self):
        renderer = get_export_renderer(REPORT_KEY_DAPODIK)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DAPODIK,
            format=ExportJob.FORMAT_XLSX,
            filters={'school_id': 999999},
        )
        with self.assertRaises(StatutoryExportError):
            renderer(job)

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
    get_default_field_map,
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
            ownership_status=School.OWNERSHIP_SWASTA,
            kelurahan="Menteng", kecamatan="Tebet",
            kabupaten_kota="Jakarta Selatan", provinsi="DKI Jakarta",
        )
        self.madrasah = School.all_tenants.create(
            foundation_id=self.foundation.id, name="MA Bina Bangsa",
            npsn="40100002", level=School.LEVEL_MA,
            nsm="121133010001",
            ownership_status=School.OWNERSHIP_SWASTA,
            kecamatan="Tebet", kabupaten_kota="Jakarta Selatan", provinsi="DKI Jakarta",
        )

        # Homeroom teacher with complete data
        self.teacher_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Pak Guru Wali",
            nik="3171010101010001", gender=Person.GENDER_MALE, dob=date(1985, 5, 20),
            religion=Person.RELIGION_ISLAM,
        )
        self.teacher_user = User.all_tenants.create_user(
            phone_e164="+6281888888881", foundation_id=self.foundation.id,
            full_name="Pak Guru Wali",
        )
        self.teacher = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=self.teacher_person, user=self.teacher_user,
            school=self.school, nuptk="1612345678900001", nip="198505202010011001",
            employment_type=Staff.TYPE_PERMANENT, appointment_type=Staff.APPT_PNS,
            join_date=date(2010, 1, 1),
        )

        # Student with complete data, enrolled in a rombel
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Andi Wijaya",
            nik="3171010101010002", gender=Person.GENDER_MALE, dob=date(2008, 3, 15),
            religion=Person.RELIGION_ISLAM,
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
        npsn_issues = [i for i in issues['school'] if i['field'] == 'npsn']
        self.assertEqual(len(npsn_issues), 1)
        self.assertIn('NPSN kosong', npsn_issues[0]['problem'])
        School.all_tenants.filter(id=school.id).delete()

    def test_school_profile_issues_reported(self):
        """New statutory fields: ownership, NSM (EMIS only), address completeness."""
        school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Profil Kosong",
            npsn="", level=School.LEVEL_SMA,
        )
        issues = validate_statutory_export(school, StatutorySystem.DAPODIK)
        school_fields = {i['field'] for i in issues['school']}
        self.assertIn('ownership_status', school_fields)
        self.assertIn('address', school_fields)
        # NSM is EMIS-only: not flagged on a DAPODIK run
        self.assertNotIn('nsm', school_fields)
        School.all_tenants.filter(id=school.id).delete()

    def test_emis_requires_nsm(self):
        """CMP-019: the EMIS path additionally requires the madrasah
        statistics number (NSM)."""
        # madrasah fixture has NSM set — clear it and re-validate
        self.madrasah.nsm = ''
        self.madrasah.save(update_fields=['nsm'])
        issues = validate_statutory_export(self.madrasah, StatutorySystem.EMIS)
        nsm_issues = [i for i in issues['school'] if i['field'] == 'nsm']
        self.assertEqual(len(nsm_issues), 1)
        self.assertIn('NSM kosong', nsm_issues[0]['problem'])

        # But the same school on the DAPODIK path is NOT flagged for NSM
        issues_dapodik = validate_statutory_export(self.madrasah, StatutorySystem.DAPODIK)
        dapodik_fields = {i['field'] for i in issues_dapodik['school']}
        self.assertNotIn('nsm', dapodik_fields)

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
        self.assertEqual(students[0]['agama'], 'ISLAM')

        staff = bundle['sheets']['staff']
        self.assertEqual(len(staff), 1)
        self.assertEqual(staff[0]['nama'], 'Pak Guru Wali')
        self.assertEqual(staff[0]['nuptk'], '1612345678900001')
        self.assertEqual(staff[0]['kepegawaian'], 'PNS')

    def test_expanded_statutory_fields_exported(self):
        """New CMP-017 fields flow into the bundle's rows."""
        self.student_person.birth_city = 'Jakarta'
        self.student_person.birth_certificate_number = 'ACT-2008-998877'
        self.student_person.rt = '003'
        self.student_person.rw = '005'
        self.student_person.kelurahan = 'Menteng'
        self.student_person.kecamatan = 'Tebet'
        self.student_person.kabupaten_kota = 'Jakarta Selatan'
        self.student_person.provinsi = 'DKI Jakarta'
        self.student_person.postal_code = '12810'
        self.student_person.save()

        self.teacher.highest_degree = 'S1'
        self.teacher.degree_institution = 'Universitas Pendidikan Indonesia'
        self.teacher.degree_graduation_year = 2008
        self.teacher.certification_status = Staff.CERT_SERTIFIKAT
        self.teacher.save()

        exporter = DapodikFileExporter(school=self.school)
        bundle = exporter.build_bundle()

        student_row = bundle['sheets']['students'][0]
        self.assertEqual(student_row['tempat_lahir'], 'Jakarta')
        self.assertEqual(student_row['nomor_akta_kelahiran'], 'ACT-2008-998877')
        self.assertEqual(student_row['rt'], '003')
        self.assertEqual(student_row['kelurahan'], 'Menteng')
        self.assertEqual(student_row['kode_pos'], '12810')

        staff_row = bundle['sheets']['staff'][0]
        self.assertEqual(staff_row['pendidikan_tertinggi'], 'S1')
        self.assertEqual(staff_row['institusi'], 'Universitas Pendidikan Indonesia')
        # _resolve stringifies values for the sheet rows
        self.assertEqual(staff_row['tahun_lulus'], '2008')
        self.assertEqual(staff_row['status_sertifikasi'], 'SERTIFIKAT')

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


class DapodikMotherNameAndCoordinatesTests(StatutoryExportFixtures):
    """Tests for mother_name and home coordinates (lat/long) statutory export & validation."""

    def test_default_field_map_includes_mother_name_and_coordinates(self):
        field_map = get_default_field_map()

        # Student sheet mappings
        self.assertIn('person.mother_name', field_map['students'])
        self.assertEqual(field_map['students']['person.mother_name'], 'nama_ibu_kandung')
        self.assertIn('person.home_latitude', field_map['students'])
        self.assertEqual(field_map['students']['person.home_latitude'], 'lintang')
        self.assertIn('person.home_longitude', field_map['students'])
        self.assertEqual(field_map['students']['person.home_longitude'], 'bujur')

        # Staff sheet mappings
        self.assertIn('person.home_latitude', field_map['staff'])
        self.assertEqual(field_map['staff']['person.home_latitude'], 'lintang')
        self.assertIn('person.home_longitude', field_map['staff'])
        self.assertEqual(field_map['staff']['person.home_longitude'], 'bujur')

    def test_dapodik_bundle_exports_mother_name_and_coordinates(self):
        self.student_person.mother_name = 'Siti Aminah'
        self.student_person.home_latitude = Decimal('-6.208800')
        self.student_person.home_longitude = Decimal('106.845600')
        self.student_person.save()

        self.teacher_person.home_latitude = Decimal('-6.175392')
        self.teacher_person.home_longitude = Decimal('106.827153')
        self.teacher_person.save()

        exporter = DapodikFileExporter(school=self.school)
        bundle = exporter.build_bundle()

        student_row = bundle['sheets']['students'][0]
        self.assertEqual(student_row['nama_ibu_kandung'], 'Siti Aminah')
        self.assertEqual(student_row['lintang'], '-6.208800')
        self.assertEqual(student_row['bujur'], '106.845600')

        staff_row = bundle['sheets']['staff'][0]
        self.assertEqual(staff_row['lintang'], '-6.175392')
        self.assertEqual(staff_row['bujur'], '106.827153')

    def test_validate_statutory_export_unconfigured_schema_ignores_optional_fields(self):
        """When schema does not configure mandatory fields, blank mother_name/coordinates do not trigger issues."""
        # Ensure student and staff have blank mother_name and coordinates
        self.student_person.mother_name = ''
        self.student_person.home_latitude = None
        self.student_person.home_longitude = None
        self.student_person.save()

        self.teacher_person.home_latitude = None
        self.teacher_person.home_longitude = None
        self.teacher_person.save()

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        # Standard fixtures have valid student and staff data; no mother_name/geo issues should exist
        student_fields_with_issues = [i['field'] for i in issues['students']]
        self.assertNotIn('person.mother_name', student_fields_with_issues)
        self.assertNotIn('person.home_latitude', student_fields_with_issues)
        self.assertNotIn('person.home_longitude', student_fields_with_issues)

        staff_fields_with_issues = [i['field'] for i in issues['staff']]
        self.assertNotIn('person.home_latitude', staff_fields_with_issues)
        self.assertNotIn('person.home_longitude', staff_fields_with_issues)

    def test_validate_statutory_export_mandatory_fields_reports_missing(self):
        """When schema configures mandatory fields, missing values are reported with name and rombel."""
        schema = StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK,
            version='2026.mandatory',
            is_active=True,
            mandatory_fields={
                'students': ['person.mother_name', 'person.home_latitude', 'person.home_longitude'],
                'staff': ['person.home_latitude', 'person.home_longitude'],
            },
        )

        self.student_person.mother_name = ''
        self.student_person.home_latitude = None
        self.student_person.home_longitude = None
        self.student_person.save()

        self.teacher_person.home_latitude = None
        self.teacher_person.home_longitude = None
        self.teacher_person.save()

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)

        # Check student issues
        student_issue_map = {i['field']: i for i in issues['students']}
        self.assertIn('person.mother_name', student_issue_map)
        self.assertEqual(student_issue_map['person.mother_name']['record'], 'Andi Wijaya')
        self.assertEqual(student_issue_map['person.mother_name']['rombel'], 'X IPA 1')
        self.assertIn('Nama ibu kandung kosong', student_issue_map['person.mother_name']['problem'])

        self.assertIn('person.home_latitude', student_issue_map)
        self.assertEqual(student_issue_map['person.home_latitude']['record'], 'Andi Wijaya')
        self.assertEqual(student_issue_map['person.home_latitude']['rombel'], 'X IPA 1')

        self.assertIn('person.home_longitude', student_issue_map)
        self.assertEqual(student_issue_map['person.home_longitude']['record'], 'Andi Wijaya')
        self.assertEqual(student_issue_map['person.home_longitude']['rombel'], 'X IPA 1')

        # Check staff issues
        staff_issue_map = {i['field']: i for i in issues['staff']}
        self.assertIn('person.home_latitude', staff_issue_map)
        self.assertEqual(staff_issue_map['person.home_latitude']['record'], 'Pak Guru Wali')
        self.assertIn('person.home_longitude', staff_issue_map)
        self.assertEqual(staff_issue_map['person.home_longitude']['record'], 'Pak Guru Wali')

    def test_validate_statutory_export_mandatory_fields_passes_when_filled(self):
        """When mandatory fields are populated, validation passes without reporting them."""
        StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK,
            version='2026.mandatory',
            is_active=True,
            mandatory_fields={
                'students': ['person.mother_name', 'person.home_latitude', 'person.home_longitude'],
                'staff': ['person.home_latitude', 'person.home_longitude'],
            },
        )

        self.student_person.mother_name = 'Siti Rahayu'
        self.student_person.home_latitude = Decimal('-6.208800')
        self.student_person.home_longitude = Decimal('106.845600')
        self.student_person.save()

        self.teacher_person.home_latitude = Decimal('-6.175392')
        self.teacher_person.home_longitude = Decimal('106.827153')
        self.teacher_person.save()

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        student_fields_with_issues = [i['field'] for i in issues['students']]
        self.assertNotIn('person.mother_name', student_fields_with_issues)
        self.assertNotIn('person.home_latitude', student_fields_with_issues)
        self.assertNotIn('person.home_longitude', student_fields_with_issues)

        staff_fields_with_issues = [i['field'] for i in issues['staff']]
        self.assertNotIn('person.home_latitude', staff_fields_with_issues)
        self.assertNotIn('person.home_longitude', staff_fields_with_issues)

    def test_validate_statutory_export_reads_mandatory_from_field_map(self):
        """Verify fallback reading from field_map['mandatory_fields']."""
        StatutoryExportSchema.all_tenants.create(
            foundation_id=self.foundation.id,
            system=StatutorySystem.DAPODIK,
            version='2026.embedded',
            is_active=True,
            field_map={
                'mandatory_fields': {
                    'students': ['person.mother_name'],
                }
            },
        )

        self.student_person.mother_name = ''
        self.student_person.save()

        issues = validate_statutory_export(self.school, StatutorySystem.DAPODIK)
        student_fields_with_issues = [i['field'] for i in issues['students']]
        self.assertIn('person.mother_name', student_fields_with_issues)


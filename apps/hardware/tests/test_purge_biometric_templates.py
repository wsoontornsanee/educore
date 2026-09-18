"""Tests for the biometric-template retention sweeper (spec/14 §3/§7, CMP-012/013)."""
from django.core.management import call_command
from django.test import TestCase

from apps.compliance.models import ConsentPurpose
from apps.compliance.services import record_consent, withdraw_biometric_consent
from apps.core.models import JobRun
from apps.hardware.models import BiometricTemplateStatus
from apps.hardware.services import enroll_biometric_template
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PurgeBiometricTemplatesTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Sapu Biometrik", brand_name="Uji Sapu", npwp="01.000.000.0-014.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Sapu", npsn="40100096", level=School.LEVEL_SMA,
        )

        self.active_person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siswa Aktif")
        self.active_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.active_person,
            nis="2026091", status=Student.STATUS_ACTIVE,
        )
        self.active_consent = record_consent(
            subject_type='STUDENT', subject_id=self.active_student.id, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        self.active_template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.active_student.id, foundation_id=self.foundation.id,
            raw_template='raw-active', consent=self.active_consent,
        )

        self.exited_person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siswa Lulus")
        self.exited_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.exited_person,
            nis="2026092", status=Student.STATUS_GRADUATED,
        )
        self.exited_consent = record_consent(
            subject_type='STUDENT', subject_id=self.exited_student.id, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        self.exited_template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.exited_student.id, foundation_id=self.foundation.id,
            raw_template='raw-exited', consent=self.exited_consent,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_dry_run_reports_without_deleting(self):
        call_command('purge_biometric_templates', '--dry-run', '--force')

        self.exited_template.refresh_from_db()
        self.assertEqual(self.exited_template.status, BiometricTemplateStatus.ACTIVE)
        self.assertNotEqual(self.exited_template.template_ciphertext, '')

    def test_purges_exited_student_template(self):
        call_command('purge_biometric_templates', '--force')

        self.exited_template.refresh_from_db()
        self.assertEqual(self.exited_template.status, BiometricTemplateStatus.PURGED)
        self.assertEqual(self.exited_template.template_ciphertext, '')
        self.assertIsNotNone(self.exited_template.purged_at)

        self.active_template.refresh_from_db()
        self.assertEqual(self.active_template.status, BiometricTemplateStatus.ACTIVE)
        self.assertNotEqual(self.active_template.template_ciphertext, '')

        job_run = JobRun.objects.filter(job_name='purge_biometric_templates').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_safety_net_catches_orphaned_withdrawn_consent(self):
        """Regression: if a template were ever left ACTIVE after its consent
        was withdrawn through some path other than withdraw_biometric_consent
        (which already deletes synchronously), the daily sweep must still
        catch it — CMP-013's "not by hope"."""
        self.active_consent.withdrawn_at = self.active_consent.granted_at
        self.active_consent.save(update_fields=['withdrawn_at'])

        call_command('purge_biometric_templates', '--force')

        self.active_template.refresh_from_db()
        self.assertEqual(self.active_template.status, BiometricTemplateStatus.WITHDRAWN)
        self.assertEqual(self.active_template.template_ciphertext, '')

    def test_runs_without_ambient_tenant_context(self):
        clear_current_foundation_id()

        call_command('purge_biometric_templates', '--force')

        self.exited_template.refresh_from_db()
        self.assertEqual(self.exited_template.status, BiometricTemplateStatus.PURGED)

    def test_purges_across_multiple_foundations(self):
        clear_current_foundation_id()
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Sapu Lain", brand_name="Sapu Lain", npwp="02.000.000.0-014.000",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMA Sapu Lain", npsn="40100093", level=School.LEVEL_SMA,
        )
        other_person = Person.all_tenants.create(foundation_id=other_foundation.id, full_name="Siswa Lain Lulus")
        other_student = Student.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school, person=other_person,
            nis="2026090", status=Student.STATUS_TRANSFERRED_OUT,
        )
        other_consent = record_consent(
            subject_type='STUDENT', subject_id=other_student.id, foundation_id=other_foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        other_template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=other_student.id, foundation_id=other_foundation.id,
            raw_template='raw-other', consent=other_consent,
        )

        call_command('purge_biometric_templates', '--force')

        self.exited_template.refresh_from_db()
        self.assertEqual(self.exited_template.status, BiometricTemplateStatus.PURGED)
        other_template.refresh_from_db()
        self.assertEqual(other_template.status, BiometricTemplateStatus.PURGED)

        job_run = JobRun.objects.filter(job_name='purge_biometric_templates').latest('started_at')
        self.assertEqual(job_run.items_processed, 2)

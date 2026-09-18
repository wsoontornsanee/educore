"""Tests for biometric enrollment consent & face-template deletion
(spec/12 §6, spec/14 §3 CMP-009/010/012).
"""
import datetime

from django.test import TestCase

from apps.compliance.models import ConsentPurpose, ConsentRecord
from apps.compliance.services import (
    ConsentNotFoundError,
    record_consent,
    withdraw_biometric_consent,
)
from apps.core.models import AuditEvent
from apps.hardware.crypto import decrypt_template
from apps.hardware.models import BiometricTemplate, BiometricTemplateStatus
from apps.hardware.services import (
    BiometricConsentRequiredError,
    active_biometric_templates_for_subject,
    enroll_biometric_template,
)
from apps.identity.models import Foundation, Person, School, Staff, Student, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ConsentRecordVersioningTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Consent", brand_name="Uji", npwp="01.000.000.0-011.000",
        )
        set_current_foundation_id(self.foundation.id)

    def tearDown(self):
        clear_current_foundation_id()

    def test_first_grant_is_version_one(self):
        consent = record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        self.assertEqual(consent.version, 1)
        self.assertTrue(consent.is_active)

    def test_purposes_are_never_bundled(self):
        """CMP-009: consenting to one purpose never implies another."""
        record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        photo_consent = record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.PHOTO_MEDIA, granted_by='7',
        )
        self.assertEqual(photo_consent.version, 1)
        self.assertEqual(
            ConsentRecord.objects.filter(
                foundation_id=self.foundation.id, subject_id=1, purpose=ConsentPurpose.BIOMETRIC,
            ).count(),
            1,
        )

    def test_re_grant_after_withdrawal_creates_new_version(self):
        first = record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        first.withdrawn_at = first.granted_at
        first.save(update_fields=['withdrawn_at'])

        second = record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        self.assertEqual(second.version, 2)
        self.assertTrue(second.is_active)

    def test_grant_writes_audit_event(self):
        consent = record_consent(
            subject_type='STUDENT', subject_id=1, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )
        self.assertTrue(
            AuditEvent.objects.filter(action='compliance.consent.grant', entity_id=str(consent.id)).exists()
        )


class BiometricEnrollmentAndWithdrawalTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Biometrik", brand_name="Uji", npwp="01.000.000.0-012.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Biometrik", npsn="40100098", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Budi Enroll", nik="3171010101018888",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026098", status=Student.STATUS_ACTIVE,
        )
        self.consent = record_consent(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.BIOMETRIC, granted_by='7',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_enrollment_requires_active_biometric_consent(self):
        photo_only_consent = record_consent(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            purpose=ConsentPurpose.PHOTO_MEDIA, granted_by='7',
        )
        with self.assertRaises(BiometricConsentRequiredError):
            enroll_biometric_template(
                subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
                raw_template='raw-face-vector-bytes', consent=photo_only_consent,
            )

    def test_enrollment_stores_encrypted_template_not_plaintext(self):
        template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            raw_template='raw-face-vector-bytes', consent=self.consent,
        )
        self.assertNotEqual(template.template_ciphertext, 'raw-face-vector-bytes')
        self.assertEqual(decrypt_template(template.template_ciphertext), 'raw-face-vector-bytes')
        self.assertEqual(template.status, BiometricTemplateStatus.ACTIVE)

    def test_withdrawal_without_consent_raises(self):
        other_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Tanpa Consent", nik="3171010101019999",
        )
        other_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=other_person,
            nis="2026099", status=Student.STATUS_ACTIVE,
        )
        with self.assertRaises(ConsentNotFoundError):
            withdraw_biometric_consent(
                subject_type='STUDENT', subject_id=other_student.id, foundation_id=self.foundation.id,
                actor_id='7',
            )

    def test_withdrawal_deletes_template_within_the_same_call(self):
        """CMP-010: templates deleted, student reverts to card-only, within 24h —
        done synchronously so the SLA is trivially satisfied."""
        template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            raw_template='raw-face-vector-bytes', consent=self.consent,
        )
        withdraw_biometric_consent(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            actor_id='7', actor_name='Ketua Yayasan',
        )
        template.refresh_from_db()
        self.assertEqual(template.template_ciphertext, '')
        self.assertEqual(template.status, BiometricTemplateStatus.WITHDRAWN)
        self.assertIsNotNone(template.withdrawn_at)

        self.consent.refresh_from_db()
        self.assertIsNotNone(self.consent.withdrawn_at)
        self.assertFalse(self.consent.is_active)

    def test_withdrawal_writes_audit_events(self):
        enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            raw_template='raw-face-vector-bytes', consent=self.consent,
        )
        withdraw_biometric_consent(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            actor_id='7',
        )
        self.assertTrue(AuditEvent.objects.filter(action='compliance.consent.withdraw').exists())
        self.assertTrue(AuditEvent.objects.filter(action='hardware.biometric.delete').exists())

    def test_active_templates_helper_excludes_withdrawn(self):
        template = enroll_biometric_template(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            raw_template='raw-face-vector-bytes', consent=self.consent,
        )
        self.assertEqual(
            list(active_biometric_templates_for_subject('STUDENT', self.student.id, self.foundation.id)),
            [template],
        )
        withdraw_biometric_consent(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id, actor_id='7',
        )
        self.assertEqual(
            list(active_biometric_templates_for_subject('STUDENT', self.student.id, self.foundation.id)), [],
        )

    def test_cross_tenant_withdrawal_is_refused(self):
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="01.000.000.0-013.000",
        )
        with self.assertRaises(ConsentNotFoundError):
            withdraw_biometric_consent(
                subject_type='STUDENT', subject_id=self.student.id, foundation_id=other_foundation.id,
                actor_id='7',
            )

    def test_has_active_health_consent_true_after_grant(self):
        from apps.compliance.services import has_active_health_consent
        from apps.compliance.models import ConsentPurpose, DataSubjectRequestSubjectType
        from apps.compliance.services import record_consent

        record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        self.assertTrue(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))

    def test_has_active_health_consent_false_when_none_granted(self):
        from apps.compliance.services import has_active_health_consent
        from apps.compliance.models import DataSubjectRequestSubjectType

        self.assertFalse(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))

    def test_has_active_health_consent_false_after_withdrawal(self):
        from apps.compliance.services import has_active_health_consent, record_consent
        from apps.compliance.models import ConsentPurpose, ConsentRecord, DataSubjectRequestSubjectType
        from django.utils import timezone

        record = record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        record.withdrawn_at = timezone.now()
        record.save(update_fields=['withdrawn_at'])

        self.assertFalse(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))

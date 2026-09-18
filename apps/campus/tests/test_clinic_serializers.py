import datetime

from django.test import TestCase

from apps.academic.tests.base import build_academic_fixture
from apps.campus.crypto import encrypt_note
from apps.campus.models import ClinicOutcome, ClinicVisit, MedicationStock
from apps.campus.serializers import ClinicVisitSerializer, RecordClinicVisitInputSerializer


class ClinicSerializerTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_clinic_visit_serializer_decrypts_complaint(self):
        visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            complaint_encrypted=encrypt_note('Sakit perut'),
            treatment_encrypted=encrypt_note('Diberi obat maag'),
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx['teacher'],
        )
        data = ClinicVisitSerializer(visit).data
        self.assertEqual(data['complaint'], 'Sakit perut')
        self.assertEqual(data['treatment'], 'Diberi obat maag')
        self.assertNotIn('complaint_encrypted', data)

    def test_record_clinic_visit_input_serializer_valid(self):
        serializer = RecordClinicVisitInputSerializer(data={
            'student_id': self.fx['student'].id,
            'complaint': 'Demam',
            'outcome': ClinicOutcome.SENT_HOME,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['guardian_consent_confirmed'], False)

    def test_record_clinic_visit_input_serializer_rejects_bad_outcome(self):
        serializer = RecordClinicVisitInputSerializer(data={
            'student_id': self.fx['student'].id,
            'complaint': 'Demam',
            'outcome': 'RESTING_IN_UKS',
        })
        self.assertFalse(serializer.is_valid())

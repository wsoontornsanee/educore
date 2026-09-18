import datetime

from django.test import TestCase

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import ClinicOutcome, ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock


class ClinicModelTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_clinic_policy_defaults(self):
        policy = ClinicPolicy.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
        )
        self.assertTrue(policy.teacher_sees_allergies)

    def test_health_profile_has_medical_alert_false_when_empty(self):
        profile = HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
        )
        self.assertFalse(profile.has_medical_alert)

    def test_health_profile_has_medical_alert_true_with_allergies(self):
        profile = HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            allergies=['Penisilin'],
        )
        self.assertTrue(profile.has_medical_alert)

    def test_medication_stock_creation(self):
        stock = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            name='Paracetamol 500mg',
            unit='tablet',
            quantity=100,
            expiry_date=datetime.date(2027, 1, 1),
            reorder_level=20,
        )
        self.assertEqual(stock.quantity, 100)

    def test_clinic_visit_creation(self):
        visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            complaint_encrypted='ciphertext-placeholder',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx['teacher'],
        )
        self.assertEqual(visit.outcome, ClinicOutcome.RETURNED_TO_CLASS)
        self.assertIsNone(visit.guardian_notified_at)

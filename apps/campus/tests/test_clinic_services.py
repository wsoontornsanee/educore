import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import MedicationStock
from apps.campus.services_clinic import (
    get_medication_stock_alerts,
    get_or_create_clinic_policy,
    get_or_create_health_profile,
)


class ClinicPolicyAndAlertServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_get_or_create_clinic_policy_is_idempotent(self):
        p1 = get_or_create_clinic_policy(self.fx['school'])
        p2 = get_or_create_clinic_policy(self.fx['school'])
        self.assertEqual(p1.id, p2.id)

    def test_get_or_create_health_profile_is_idempotent(self):
        h1 = get_or_create_health_profile(self.fx['student'])
        h2 = get_or_create_health_profile(self.fx['student'])
        self.assertEqual(h1.id, h2.id)

    def test_medication_stock_alerts_below_reorder_level(self):
        low = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Oralit', unit='sachet', quantity=2, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Perban', unit='pcs', quantity=50, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(low, list(alerts))
        self.assertEqual(alerts.count(), 1)

    def test_medication_stock_alerts_near_expiry(self):
        near_expiry = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Antiseptik', unit='botol', quantity=100, reorder_level=5,
            expiry_date=(timezone.now().date() + datetime.timedelta(days=10)),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(near_expiry, list(alerts))

    def test_medication_stock_alerts_excludes_healthy_stock(self):
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Vitamin C', unit='tablet', quantity=200, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertEqual(alerts.count(), 0)

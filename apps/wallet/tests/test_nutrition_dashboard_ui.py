"""Parent Nutrition Dashboard Component and Access Tests (spec/07 WAL-024, spec/08 PAR-010, spec/17)."""
from datetime import date
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import (
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    Student,
    User,
)
from apps.identity.rbac import ROLE_PARENT, SCOPE_SCHOOL, assign_role
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import set_current_foundation_id


class ParentNutritionDashboardComponentTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture("Yayasan Nutrisi Test")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)

    def test_component_renders_with_active_nutrition_summary(self):
        summary_data = {
            'student_id': self.student.id,
            'from_date': '2026-09-10',
            'to_date': '2026-09-16',
            'total_calories': 1450,
            'total_sugar_g': Decimal('24.50'),
            'total_items': 10,
            'healthy_items_count': 7,
            'allergens': ['kacang', 'susu'],
            'daily_breakdown': [
                {
                    'date': '2026-09-10',
                    'total_calories': 350,
                    'total_sugar_g': Decimal('6.00'),
                    'items_count': 2,
                    'healthy_count': 2,
                },
                {
                    'date': '2026-09-11',
                    'total_calories': 400,
                    'total_sugar_g': Decimal('8.50'),
                    'items_count': 3,
                    'healthy_count': 2,
                },
            ],
            'items': [
                {
                    'sku': 'BUAH-01',
                    'name': 'Apel Fuji Segar',
                    'qty': 1,
                    'unit_price': '5000.00',
                    'calories': 95,
                    'sugar_g': '18.00',
                    'allergens': [],
                    'is_healthy': True,
                    'occurred_at': timezone.now(),
                },
                {
                    'sku': 'ROTI-01',
                    'name': 'Roti Kacang Tanah',
                    'qty': 1,
                    'unit_price': '8000.00',
                    'calories': 250,
                    'sugar_g': '12.00',
                    'allergens': ['kacang'],
                    'is_healthy': False,
                    'occurred_at': timezone.now(),
                },
            ],
        }

        html = render_to_string(
            'components/parent_nutrition_dashboard.html',
            {
                'student': self.student,
                'summary': summary_data,
                'period': 'WEEK',
                'linked_students': [self.student],
            },
        )

        # Assert key elements exist
        self.assertIn("Nutrisi & Asupan Harian", html)
        self.assertIn(self.student.person.full_name, html)
        self.assertIn("1450", html)
        self.assertIn("24,50", html)
        self.assertIn("PILIHAN SEHAT", html)
        self.assertIn("ALERGEN TERDETEKSI", html)
        self.assertIn("Peringatan Kandungan Alergen", html)
        self.assertIn("KACANG", html)
        self.assertIn("Apel Fuji Segar", html)
        self.assertIn("Roti Kacang Tanah", html)

    def test_component_renders_empty_state_gracefully(self):
        empty_summary = {
            'student_id': self.student.id,
            'from_date': '2026-09-16',
            'to_date': '2026-09-16',
            'total_calories': 0,
            'total_sugar_g': Decimal('0.00'),
            'total_items': 0,
            'healthy_items_count': 0,
            'allergens': [],
            'daily_breakdown': [],
            'items': [],
        }

        html = render_to_string(
            'components/parent_nutrition_dashboard.html',
            {
                'student': self.student,
                'summary': empty_summary,
                'period': 'TODAY',
            },
        )

        self.assertIn("Belum Ada Riwayat Konsumsi Kantin", html)
        self.assertIn("Lihat Riwayat 30 Hari Terakhir", html)


class ParentNutritionEndpointAccessTests(TestCase):
    """Test parent role authorization and multi-tenant boundary for nutrition summary."""

    def setUp(self):
        self.client = APIClient()
        self.fx_a = build_wallet_fixture("Yayasan Nutrisi A")
        self.fx_b = build_wallet_fixture("Yayasan Nutrisi B")

        self.foundation_a = self.fx_a['foundation']
        self.school_a = self.fx_a['school']
        self.student_a = self.fx_a['student']

        self.foundation_b = self.fx_b['foundation']
        self.school_b = self.fx_b['school']
        self.student_b = self.fx_b['student']

        set_current_foundation_id(self.foundation_a.id)

        # Parent User in Foundation A
        self.parent_user_a = User.objects.create(
            foundation_id=self.foundation_a.id,
            phone_e164="+628111222333",
            email="parent_a@school.id",
            full_name="Budi Santoso",
        )
        assign_role(
            user=self.parent_user_a,
            role=ROLE_PARENT,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a.id,
            foundation_id=self.foundation_a.id,
        )

        self.person_guardian_a = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Budi Santoso",
            nik="3201010101010099",
            gender="L",
            dob=date(1980, 1, 1),
        )
        self.guardian_a = Guardian.all_tenants.create(
            foundation_id=self.foundation_a.id,
            user=self.parent_user_a,
            person=self.person_guardian_a,
        )

        GuardianLink.all_tenants.create(
            foundation_id=self.foundation_a.id,
            guardian=self.guardian_a,
            student=self.student_a,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

    def test_linked_parent_can_access_student_nutrition_summary(self):
        self.client.force_authenticate(user=self.parent_user_a)
        url = f"/api/v1/students/{self.student_a.id}/nutrition-summary/"
        resp = self.client.get(url, HTTP_X_FOUNDATION_ID=str(self.foundation_a.id))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['student_id'], self.student_a.id)
        self.assertEqual(resp.data['total_calories'], 0)
        self.assertEqual(resp.data['total_items'], 0)

    def test_unlinked_parent_cannot_access_other_student_summary(self):
        # Create unlinked student in same foundation A
        person_other = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Anak Lain",
            nik="3201010101010033",
            gender="L",
            dob=date(2015, 4, 4),
        )
        student_other = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a,
            person=person_other,
            nis="2026333",
            nisn="0073333333",
            status=Student.STATUS_ACTIVE,
        )

        self.client.force_authenticate(user=self.parent_user_a)
        url = f"/api/v1/students/{student_other.id}/nutrition-summary/"
        resp = self.client.get(url, HTTP_X_FOUNDATION_ID=str(self.foundation_a.id))

        # Fail-closed: 404 Not Found for unlinked student
        self.assertEqual(resp.status_code, 404)

    def test_cross_tenant_student_returns_404(self):
        self.client.force_authenticate(user=self.parent_user_a)
        url = f"/api/v1/students/{self.student_b.id}/nutrition-summary/"
        resp = self.client.get(url, HTTP_X_FOUNDATION_ID=str(self.foundation_a.id))

        # Cross-tenant is strictly 404
        self.assertEqual(resp.status_code, 404)

"""Tests for GET /me/children/ (spec/08 PAR-003, PAR-017)."""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import (
    Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Student, User,
)
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import tenant_context


class GuardianChildrenEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Nusantara", brand_name="Nusantara")
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Nusantara", npsn="87654321", level="SMP"
        )
        self.guardian_user = User.all_tenants.create_user(
            phone_e164="+6281200000010", full_name="Bapak Joko", foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.guardian_user,
            role=RoleAssignment.ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Joko Susilo")
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.guardian_user
        )
        student_person_a = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Dewi Kecil")
        self.student_financial = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person_a, nis="2026010"
        )
        student_person_b = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Budi Kecil")
        self.student_non_financial = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=student_person_b, nis="2026011"
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student_financial,
            financial_responsible=True,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=self.guardian, student=self.student_non_financial,
            financial_responsible=False,
        )

    def _auth(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_returns_only_linked_children_with_financial_flag(self):
        self._auth(self.guardian_user)
        response = self.client.get('/api/v1/me/children/')
        self.assertEqual(response.status_code, 200)
        by_id = {row['student_id']: row for row in response.data}
        self.assertEqual(len(by_id), 2)
        self.assertTrue(by_id[self.student_financial.id]['financial_responsible'])
        self.assertFalse(by_id[self.student_non_financial.id]['financial_responsible'])

    def test_staff_user_gets_403(self):
        with tenant_context(self.foundation.id):
            staff_user, staff_person = create_user_with_person(
                foundation_id=self.foundation.id, full_name="Guru Andi", phone="081200000099",
            )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=staff_user,
            role=RoleAssignment.ROLE_TEACHER, scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )
        self._auth(staff_user)
        response = self.client.get('/api/v1/me/children/')
        self.assertEqual(response.status_code, 403)

    def test_cross_foundation_isolation(self):
        """Verify endpoint does not leak children from other foundations (spec/02 §4, spec/08 PAR-017)."""
        # Create a second foundation with its own school and student
        foundation_b = Foundation.objects.create(legal_name="Yayasan Merdeka", brand_name="Merdeka")
        school_b = School.all_tenants.create(
            foundation_id=foundation_b.id, name="SMP Merdeka", npsn="12345678", level="SMP"
        )

        # Create a Guardian profile for the SAME user in foundation B
        person_b = Person.all_tenants.create(foundation_id=foundation_b.id, full_name="Joko Susilo B")
        guardian_b = Guardian.all_tenants.create(
            foundation_id=foundation_b.id, person=person_b, user=self.guardian_user
        )

        # Create a student in foundation B
        student_person_b_child = Person.all_tenants.create(foundation_id=foundation_b.id, full_name="Anak B")
        student_b = Student.all_tenants.create(
            foundation_id=foundation_b.id, school=school_b, person=student_person_b_child, nis="2026099"
        )

        # Link the foundation B student to the foundation B guardian
        GuardianLink.all_tenants.create(
            foundation_id=foundation_b.id, guardian=guardian_b, student=student_b,
            financial_responsible=True,
        )

        # Authenticate as the user (whose foundation_id is foundation A)
        self._auth(self.guardian_user)
        response = self.client.get('/api/v1/me/children/')
        self.assertEqual(response.status_code, 200)

        # Verify ONLY foundation A children are returned; foundation B child must NOT appear
        student_ids = {row['student_id'] for row in response.data}
        self.assertIn(self.student_financial.id, student_ids, "Foundation A child must be returned")
        self.assertIn(self.student_non_financial.id, student_ids, "Foundation A child must be returned")
        self.assertNotIn(student_b.id, student_ids, "Foundation B child must NOT be returned")
        self.assertEqual(len(student_ids), 2, "Only foundation A children should be in response")

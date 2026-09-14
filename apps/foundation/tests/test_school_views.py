"""Tests for School management API and multi-tenancy enforcement (spec/02 §7, spec/03)."""
from rest_framework import status
from rest_framework.test import APITestCase
from apps.core.models import AuditEvent
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import (
    assign_role, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER,
    SCOPE_FOUNDATION, SCOPE_SCHOOL
)
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class SchoolViewSetTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        # Foundation Alpha
        self.foundation_alpha = Foundation.objects.create(
            legal_name="Yayasan Alpha Nusantara",
            brand_name="Alpha Nusantara",
        )
        self.school_a1 = School.all_tenants.create(
            foundation_id=self.foundation_alpha.id,
            name="SD Alpha Nusantara",
            npsn="10100001",
            level=School.LEVEL_SD,
        )
        self.school_a2 = School.all_tenants.create(
            foundation_id=self.foundation_alpha.id,
            name="SMP Alpha Nusantara",
            npsn="10100002",
            level=School.LEVEL_SMP,
        )

        # Foundation Beta
        self.foundation_beta = Foundation.objects.create(
            legal_name="Yayasan Beta Cendekia",
            brand_name="Beta Cendekia",
        )
        self.school_b1 = School.all_tenants.create(
            foundation_id=self.foundation_beta.id,
            name="SMA Beta Cendekia",
            npsn="20200001",
            level=School.LEVEL_SMA,
        )

        # Users
        self.admin_alpha = User.all_tenants.create_user(
            phone_e164="+6281111111111",
            foundation_id=self.foundation_alpha.id,
            full_name="Admin Yayasan Alpha",
        )
        assign_role(
            user=self.admin_alpha,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation_alpha.id,
            foundation_id=self.foundation_alpha.id,
        )

        self.school_admin_a1 = User.all_tenants.create_user(
            phone_e164="+6282222222222",
            foundation_id=self.foundation_alpha.id,
            full_name="Kepala Sekolah SD Alpha",
        )
        assign_role(
            user=self.school_admin_a1,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a1.id,
            foundation_id=self.foundation_alpha.id,
        )

        self.teacher_a1 = User.all_tenants.create_user(
            phone_e164="+6283333333333",
            foundation_id=self.foundation_alpha.id,
            full_name="Guru Kelas Alpha",
        )
        assign_role(
            user=self.teacher_a1,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a1.id,
            foundation_id=self.foundation_alpha.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_milestone_m1_foundation_admin_sees_schools(self):
        """Milestone M1 Acceptance Test: Foundation admin logs in and sees schools."""
        self.client.force_authenticate(user=self.admin_alpha)

        with tenant_context(self.foundation_alpha.id):
            response = self.client.get('/api/v1/schools/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # Check response results (CursorPagination format: {'results': [...]})
            data = response.json()
            results = data.get('results', data)
            self.assertEqual(len(results), 2)
            npsns = {s['npsn'] for s in results}
            self.assertEqual(npsns, {"10100001", "10100002"})

    def test_layer_3_cross_tenant_isolation(self):
        """Layer 3 Tenancy Isolation: Never expose another foundation's schools."""
        self.client.force_authenticate(user=self.admin_alpha)

        with tenant_context(self.foundation_alpha.id):
            # Querying school list must not include Beta schools
            response = self.client.get('/api/v1/schools/')
            results = response.json().get('results', response.json())
            for school in results:
                self.assertNotEqual(school['id'], self.school_b1.id)
                self.assertEqual(school['foundation_id'], self.foundation_alpha.id)

            # Direct access to Beta school ID MUST return 404 (do not leak existence)
            detail_response = self.client.get(f'/api/v1/schools/{self.school_b1.id}/')
            self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_school_scope_permission_isolation_iam_012(self):
        """School-scoped admin cannot access sibling school (IAM-012)."""
        self.client.force_authenticate(user=self.school_admin_a1)

        with tenant_context(self.foundation_alpha.id):
            # Access to own school A1 -> 200 OK
            res_a1 = self.client.get(f'/api/v1/schools/{self.school_a1.id}/')
            self.assertEqual(res_a1.status_code, status.HTTP_200_OK)

            # Access to sibling school A2 -> 403 Forbidden
            res_a2 = self.client.get(f'/api/v1/schools/{self.school_a2.id}/')
            self.assertEqual(res_a2.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthorized_role_denied_school_config(self):
        """Teacher without school_config permission is denied access."""
        self.client.force_authenticate(user=self.teacher_a1)

        with tenant_context(self.foundation_alpha.id):
            response = self.client.get('/api/v1/schools/')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_creation_and_audit(self):
        """Foundation admin creates a school, ensuring audit event generation."""
        self.client.force_authenticate(user=self.admin_alpha)

        payload = {
            "name": "SMA Alpha Nusantara",
            "npsn": "10100003",
            "level": "SMA",
            "curriculum": "KURIKULUM_MERDEKA",
            "timezone": "Asia/Jakarta",
            "base_currency": "IDR",
        }

        with tenant_context(self.foundation_alpha.id):
            response = self.client.post('/api/v1/schools/', data=payload)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            school_id = response.data['id']
            self.assertEqual(response.data['foundation_id'], self.foundation_alpha.id)

            # Assert AuditEvent
            audit_events = AuditEvent.objects.filter(
                entity_type="School",
                entity_id=str(school_id),
                action="identity.school.created"
            )
            self.assertEqual(audit_events.count(), 1)
            event = audit_events.first()
            self.assertEqual(event.actor_id, str(self.admin_alpha.id))
            self.assertEqual(event.foundation_id, self.foundation_alpha.id)

    def test_school_soft_deletion_and_audit(self):
        """Soft delete school via DELETE /api/v1/schools/:id/."""
        self.client.force_authenticate(user=self.admin_alpha)

        with tenant_context(self.foundation_alpha.id):
            response = self.client.delete(f'/api/v1/schools/{self.school_a1.id}/')
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

            # Assert soft-deleted in DB
            self.school_a1.refresh_from_db()
            self.assertTrue(self.school_a1.is_deleted)
            self.assertIsNotNone(self.school_a1.deleted_at)

            # Subsequent GET returns 404
            get_res = self.client.get(f'/api/v1/schools/{self.school_a1.id}/')
            self.assertEqual(get_res.status_code, status.HTTP_404_NOT_FOUND)

            # Assert AuditEvent
            self.assertTrue(AuditEvent.objects.filter(
                entity_type="School",
                entity_id=str(self.school_a1.id),
                action="identity.school.deleted"
            ).exists())

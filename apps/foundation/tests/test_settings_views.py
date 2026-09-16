"""Tests for Foundation governance settings API, incl. configurable approval_threshold (FND-007, FIN-007, FIN-032)."""
from decimal import Decimal
from rest_framework import status
from rest_framework.test import APITestCase
from apps.core.models import AuditEvent
from apps.identity.models import Foundation, User
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, SCOPE_SCHOOL
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class FoundationSettingsViewTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Cendekia Nusantara",
            brand_name="Cendekia Nusantara",
        )

        self.admin = User.all_tenants.create_user(
            phone_e164="+6281200000001",
            foundation_id=self.foundation.id,
            full_name="Admin Yayasan Cendekia",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        self.teacher = User.all_tenants.create_user(
            phone_e164="+6281200000002",
            foundation_id=self.foundation.id,
            full_name="Guru Cendekia",
        )
        assign_role(
            user=self.teacher,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=1,
            foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_default_approval_threshold_visible_to_admin(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/settings')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(Decimal(response.data['approval_threshold']), Decimal('1000000.00'))

    def test_admin_can_update_approval_threshold_with_audit(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.patch(
                '/api/v1/foundation/settings',
                data={'approval_threshold': '2500000.00'},
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(Decimal(response.data['approval_threshold']), Decimal('2500000.00'))

            self.foundation.refresh_from_db()
            self.assertEqual(self.foundation.approval_threshold, Decimal('2500000.00'))

            event = AuditEvent.objects.filter(
                entity_type="Foundation",
                entity_id=str(self.foundation.id),
                action="foundation.settings.updated",
            ).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.diff['approval_threshold']['after'], '2500000.00')

    def test_non_admin_cannot_update_settings(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.patch(
                '/api/v1/foundation/settings',
                data={'approval_threshold': '9999999.00'},
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

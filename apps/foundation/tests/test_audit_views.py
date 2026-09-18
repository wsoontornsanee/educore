"""Tests for the Foundation Audit Explorer list view (FND-010, spec/03 §2/§5)."""
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, School, User
from apps.identity.rbac import (
    assign_role, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, SCOPE_SCHOOL,
)
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class FoundationAuditEventViewTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa", brand_name="Bina Bangsa", npwp="01.888.777.6-001.000",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="02.111.222.3-001.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Bina Bangsa", npsn="40100001", level=School.LEVEL_SMA,
        )
        self.other_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Bina Bangsa", npsn="40100002", level=School.LEVEL_SMP,
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

        self.event_finance = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=self.school.id, actor_id=str(self.admin.id),
            action='finance.invoice.issue', entity_type='Invoice', entity_id='1',
        )
        self.event_identity = AuditEvent.objects.create(
            foundation_id=self.foundation.id, school_id=self.other_school.id, actor_id='999',
            action='identity.school.updated', entity_type='School', entity_id=str(self.other_school.id),
        )
        self.event_other_foundation = AuditEvent.objects.create(
            foundation_id=self.other_foundation.id, actor_id=str(self.admin.id), action='finance.invoice.issue',
            entity_type='Invoice', entity_id='2',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_teacher_forbidden(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_lists_only_current_foundation_events(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            ids = {row['id'] for row in response.data['results']}
            self.assertEqual(ids, {self.event_finance.id, self.event_identity.id})

    def test_school_scoped_viewer_sees_only_their_schools_events(self):
        school_admin = User.all_tenants.create_user(
            phone_e164="+6281999999997", foundation_id=self.foundation.id, full_name="Admin Sekolah",
        )
        assign_role(
            user=school_admin, role=ROLE_SCHOOL_ADMIN, scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id, foundation_id=self.foundation.id,
        )
        school_less = AuditEvent.objects.create(
            foundation_id=self.foundation.id, actor_id='1', action='foundation.settings.updated',
            entity_type='Foundation', entity_id='1',
        )
        self.client.force_authenticate(user=school_admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual({row['id'] for row in response.data['results']}, {self.event_finance.id})
            # a `school` filter can only narrow within the ceiling, never reach another school
            response = self.client.get('/api/v1/foundation/audit', {'school': self.other_school.id})
            self.assertEqual(response.data['results'], [])

    def test_filter_by_module(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit?module=finance')
            self.assertEqual([row['id'] for row in response.data['results']], [self.event_finance.id])

    def test_filter_by_action(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit?action=identity.school.updated')
            self.assertEqual([row['id'] for row in response.data['results']], [self.event_identity.id])

    def test_filter_by_actor(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get(f'/api/v1/foundation/audit?actor={self.admin.id}')
            self.assertEqual([row['id'] for row in response.data['results']], [self.event_finance.id])

    def test_filter_by_school(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get(f'/api/v1/foundation/audit?school={self.other_school.id}')
            self.assertEqual([row['id'] for row in response.data['results']], [self.event_identity.id])

    def test_filter_by_entity(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit?entity_type=Invoice&entity_id=1')
            self.assertEqual([row['id'] for row in response.data['results']], [self.event_finance.id])

    def test_filter_by_date_range_excludes_out_of_range(self):
        self.client.force_authenticate(user=self.admin)
        future = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()
        with tenant_context(self.foundation.id):
            response = self.client.get(f'/api/v1/foundation/audit?from={future}')
            self.assertEqual(response.data['results'], [])

    def test_malformed_school_param_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit?school=abc')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_date_param_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit?from=not-a-date')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cross_tenant_events_never_visible(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/audit')
            ids = {row['id'] for row in response.data['results']}
            self.assertNotIn(self.event_other_foundation.id, ids)

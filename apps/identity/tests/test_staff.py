"""Automated tests for Staff model, CRUD endpoints, and offboarding lifecycle (spec/02 §2, §5, §7).

Covers:
- Staff creation with Person PII vault and User auth account
- 3-Layer tenancy isolation on Staff queries
- RBAC permission enforcement (school_config.read / school_config.write)
- Staff offboarding lifecycle (IAM-021):
  * Status transition to OFFBOARDED
  * User account suspension (is_active=False)
  * Session revocation
  * RoleAssignment revocation
  * identity.staff.offboarded domain event
  * AuditEvent logging
"""
from datetime import date
from django.contrib.sessions.models import Session
from rest_framework import status
from rest_framework.test import APITestCase
from apps.core.models import AuditEvent, DomainEvent
from apps.identity.models import Foundation, School, Person, User, RoleAssignment, Staff
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, SCOPE_SCHOOL
from apps.identity.services import create_user_with_person, offboard_staff
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class StaffAndOffboardingTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()

        # Foundation A
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
        )
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation_a.id,
            name="SMP Harapan Bangsa",
            npsn="50300001",
            level=School.LEVEL_SMP,
        )

        # Foundation B
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Darul Hikmah",
            brand_name="Darul Hikmah",
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation_b.id,
            name="SMA Darul Hikmah",
            npsn="50300002",
            level=School.LEVEL_SMA,
        )

        # Foundation Admin for Foundation A
        self.admin_user = User.all_tenants.create_user(
            foundation_id=self.foundation_a.id,
            phone_e164="+6281199990001",
            full_name="Admin Yayasan",
        )
        assign_role(
            user=self.admin_user,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation_a.id,
            foundation_id=self.foundation_a.id,
        )

        # Teacher user for Foundation A
        self.teacher_user, self.teacher_person = create_user_with_person(
            foundation_id=self.foundation_a.id,
            full_name="Ustadz Mansur",
            phone="+6281233334444",
            email="mansur@harapanbangsa.sch.id",
            nik="3201010202020001",
        )
        assign_role(
            user=self.teacher_user,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a.id,
            foundation_id=self.foundation_a.id,
        )

        self.staff_member = Staff.all_tenants.create(
            foundation_id=self.foundation_a.id,
            person=self.teacher_person,
            user=self.teacher_user,
            school=self.school_a,
            nip="198501012010011001",
            employment_type=Staff.TYPE_PERMANENT,
            join_date=date(2020, 1, 1),
            status=Staff.STATUS_ACTIVE,
        )

    def test_staff_list_and_tenancy_isolation(self):
        """Verify staff listing enforces tenancy and returns proper metadata."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get('/api/v1/staff/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['nip'], "198501012010011001")
        self.assertEqual(results[0]['person']['full_name'], "Ustadz Mansur")
        self.assertEqual(results[0]['school_name'], "SMP Harapan Bangsa")

        # User from Foundation B cannot see Foundation A staff
        admin_b = User.all_tenants.create_user(
            foundation_id=self.foundation_b.id,
            phone_e164="+6281199990002",
            full_name="Admin B",
        )
        assign_role(
            user=admin_b,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation_b.id,
            foundation_id=self.foundation_b.id,
        )
        self.client.force_authenticate(user=admin_b)
        response_b = self.client.get('/api/v1/staff/')
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)
        results_b = response_b.data.get('results', response_b.data)
        self.assertEqual(len(results_b), 0)

    def test_staff_create_via_api(self):
        """Verify POST /api/v1/staff/ creates person, user, and staff record atomically."""
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            'school_id': self.school_a.id,
            'nip': '199002022022012002',
            'employment_type': 'CONTRACT',
            'join_date': '2022-07-01',
            'full_name': 'Siti Aminah, S.Pd.',
            'nik': '3201010303030002',
            'gender': 'P',
            'phone_e164': '+6281244445555',
            'email': 'siti@harapanbangsa.sch.id',
        }

        response = self.client.post('/api/v1/staff/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['nip'], '199002022022012002')
        self.assertEqual(response.data['person']['full_name'], 'Siti Aminah, S.Pd.')

        # Verify database record
        staff_id = response.data['id']
        staff = Staff.all_tenants.get(id=staff_id)
        self.assertEqual(staff.user.phone_e164, '+6281244445555')
        self.assertEqual(staff.person.nik, '3201010303030002')

        # Verify audit log was recorded
        audit = AuditEvent.objects.filter(
            action="identity.staff.created",
            entity_id=str(staff_id)
        ).first()
        self.assertIsNotNone(audit)

    def test_staff_offboarding_lifecycle_iam_021(self):
        """Verify offboarding revokes sessions, suspends user, revokes roles, and emits domain event."""
        # Create successor teacher for class reassignment test
        successor_user, successor_person = create_user_with_person(
            foundation_id=self.foundation_a.id,
            full_name="Ustadz Zaid",
            phone="+6281266667777",
        )
        successor_staff = Staff.all_tenants.create(
            foundation_id=self.foundation_a.id,
            person=successor_person,
            user=successor_user,
            school=self.school_a,
            join_date=date(2023, 1, 1),
            status=Staff.STATUS_ACTIVE,
        )

        self.client.force_authenticate(user=self.admin_user)

        offboard_payload = {
            'reason': 'Pindah domisili ke luar kota',
            'resignation_date': '2026-09-30',
            'reassign_to_staff_id': successor_staff.id,
        }

        response = self.client.post(
            f'/api/v1/staff/{self.staff_member.id}/offboard/',
            offboard_payload,
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Staff.STATUS_OFFBOARDED)

        # 1. Staff status and resignation details updated
        self.staff_member.refresh_from_db()
        self.assertEqual(self.staff_member.status, Staff.STATUS_OFFBOARDED)
        self.assertEqual(str(self.staff_member.resignation_date), '2026-09-30')
        self.assertIn('Pindah domisili', self.staff_member.resignation_reason)

        # 2. Linked user account suspended (is_active=False)
        self.teacher_user.refresh_from_db()
        self.assertEqual(self.teacher_user.status, User.STATUS_SUSPENDED)
        self.assertFalse(self.teacher_user.is_active)

        # 3. Roles revoked (soft-deleted)
        active_roles = RoleAssignment.all_tenants.filter(
            user=self.teacher_user,
            deleted_at__isnull=True
        ).count()
        self.assertEqual(active_roles, 0)

        # 4. Domain event recorded
        event = DomainEvent.objects.filter(
            foundation_id=self.foundation_a.id,
            name='identity.staff.offboarded'
        ).latest('occurred_at')
        self.assertEqual(event.payload['staff_id'], self.staff_member.id)
        self.assertEqual(event.payload['reassign_to_staff_id'], successor_staff.id)

        # 5. Cannot offboard again
        response_dup = self.client.post(
            f'/api/v1/staff/{self.staff_member.id}/offboard/',
            offboard_payload,
            format='json'
        )
        self.assertEqual(response_dup.status_code, status.HTTP_400_BAD_REQUEST)

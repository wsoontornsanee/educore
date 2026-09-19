"""JSON API parity for staff role grant/revoke (follow-up to PR #223).

Mirrors apps/identity/tests/test_role_admin.py's escalation-rule coverage at
the API layer: these endpoints are thin wrappers around the same
role_admin.grant_role / revoke_role_assignment service functions, so the
policy itself is not re-tested exhaustively here — only that the view wires
authentication, the school ceiling, request/response shape, and error
mapping correctly.
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User

F, S = RoleAssignment.SCOPE_FOUNDATION, RoleAssignment.SCOPE_SCHOOL


class _Base(APITestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        fid = self.foundation.id
        self.school_a = School.objects.create(foundation_id=fid, name='A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=fid, name='B', npsn='22222222', level=School.LEVEL_SMA)
        self._n = 0
        self.fadmin = self.make_user('Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN, F, fid, None)
        self.head_a = self.make_user('Head A', RoleAssignment.ROLE_SCHOOL_ADMIN, S, self.school_a.id, self.school_a)
        self.teacher_a = self.make_user('Teacher A', RoleAssignment.ROLE_TEACHER, S, self.school_a.id, self.school_a)
        self.teacher_b = self.make_user('Teacher B', RoleAssignment.ROLE_TEACHER, S, self.school_b.id, self.school_b)
        self.staff_a = Staff.all_tenants.get(user=self.teacher_a)
        self.staff_b = Staff.all_tenants.get(user=self.teacher_b)

    def make_user(self, name, role, scope_type, scope_id, school, with_staff=True):
        self._n += 1
        user = User.objects.create(phone_e164=f'+62813000080{self._n:02d}', full_name=name, foundation_id=self.foundation.id)
        if role:
            RoleAssignment.objects.create(
                foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
            )
        if with_staff:
            person = Person.objects.create(foundation_id=self.foundation.id, full_name=name)
            Staff.objects.create(
                foundation_id=self.foundation.id, person=person, user=user, school=school, join_date=timezone.localdate(),
            )
        return user

    def roles_url(self, staff):
        return f'/api/v1/staff/{staff.id}/roles/'

    def detail_url(self, staff, assignment):
        return f'/api/v1/staff/{staff.id}/roles/{assignment.id}/'


class StaffRoleListViewTests(_Base):
    def test_anonymous_is_rejected(self):
        response = self.client.get(self.roles_url(self.staff_a))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_write_permission_is_rejected(self):
        self.client.force_authenticate(user=self.teacher_a)  # plain teacher, no school_config.write
        response = self.client.get(self.roles_url(self.staff_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_out_of_ceiling_staff_is_404(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.get(self.roles_url(self.staff_b))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_lists_active_assignments(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.get(self.roles_url(self.staff_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        roles = {row['role'] for row in response.data}
        self.assertEqual(roles, {RoleAssignment.ROLE_TEACHER})


class StaffRoleGrantApiTests(_Base):
    def test_school_admin_grants_teacher_in_own_school_and_it_is_audited(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.post(
            self.roles_url(self.staff_a),
            {'role': RoleAssignment.ROLE_COUNSELLOR, 'scope_type': S, 'scope_id': self.school_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(RoleAssignment.all_tenants.filter(
            user=self.teacher_a, role=RoleAssignment.ROLE_COUNSELLOR, deleted_at__isnull=True,
        ).exists())
        event = AuditEvent.objects.get(action='identity.role.granted')
        self.assertEqual(event.actor_id, str(self.head_a.id))

    def test_repeat_grant_returns_200_without_a_second_audit(self):
        self.client.force_authenticate(user=self.head_a)
        payload = {'role': RoleAssignment.ROLE_COUNSELLOR, 'scope_type': S, 'scope_id': self.school_a.id}
        self.client.post(self.roles_url(self.staff_a), payload)
        response = self.client.post(self.roles_url(self.staff_a), payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditEvent.objects.filter(action='identity.role.granted').count(), 1)

    def test_cannot_edit_own_roles(self):
        self.client.force_authenticate(user=self.head_a)
        staff = Staff.all_tenants.get(user=self.head_a)
        response = self.client.post(
            self.roles_url(staff),
            {'role': RoleAssignment.ROLE_TEACHER, 'scope_type': S, 'scope_id': self.school_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_school_admin_cannot_escalate_to_foundation_admin(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.post(
            self.roles_url(self.staff_a),
            {'role': RoleAssignment.ROLE_FOUNDATION_ADMIN, 'scope_type': F, 'scope_id': self.foundation.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RoleAssignment.all_tenants.filter(
            user=self.teacher_a, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
        ).exists())

    def test_school_admin_cannot_grant_in_another_school(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.post(
            self.roles_url(self.staff_a),
            {'role': RoleAssignment.ROLE_COUNSELLOR, 'scope_type': S, 'scope_id': self.school_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_target_in_another_school_is_404_before_grant_logic_runs(self):
        self.client.force_authenticate(user=self.head_a)
        response = self.client.post(
            self.roles_url(self.staff_b),
            {'role': RoleAssignment.ROLE_COUNSELLOR, 'scope_type': S, 'scope_id': self.school_a.id},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_foundation_admin_can_grant_at_any_scope(self):
        self.client.force_authenticate(user=self.fadmin)
        response = self.client.post(
            self.roles_url(self.staff_b),
            {'role': RoleAssignment.ROLE_FINANCE_OFFICER, 'scope_type': S, 'scope_id': self.school_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_other_foundations_staff_id_is_404(self):
        other = Foundation.objects.create(legal_name='O', brand_name='O')
        foreign_school = School.objects.create(foundation_id=other.id, name='X', npsn='33333333', level=School.LEVEL_SMA)
        foreign_user = User.objects.create(phone_e164='+6281399991234', full_name='Foreign', foundation_id=other.id)
        foreign_person = Person.objects.create(foundation_id=other.id, full_name='Foreign')
        foreign_staff = Staff.objects.create(
            foundation_id=other.id, person=foreign_person, user=foreign_user, school=foreign_school,
            join_date=timezone.localdate(),
        )
        self.client.force_authenticate(user=self.fadmin)
        response = self.client.get(self.roles_url(foreign_staff))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class StaffRoleRevokeApiTests(_Base):
    def _grant(self, actor, staff, role, scope_type, scope_id):
        self.client.force_authenticate(user=actor)
        response = self.client.post(self.roles_url(staff), {'role': role, 'scope_type': scope_type, 'scope_id': scope_id})
        assignment_id = response.data['id']
        return RoleAssignment.all_tenants.get(id=assignment_id)

    def test_school_admin_revokes_teacher_role_in_own_school_with_audit(self):
        assignment = self._grant(self.fadmin, self.staff_a, RoleAssignment.ROLE_COUNSELLOR, S, self.school_a.id)
        self.client.force_authenticate(user=self.head_a)
        response = self.client.delete(self.detail_url(self.staff_a, assignment))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(RoleAssignment.all_tenants.filter(id=assignment.id, deleted_at__isnull=True).exists())
        self.assertEqual(AuditEvent.objects.get(action='identity.role.revoked').diff['role'], RoleAssignment.ROLE_COUNSELLOR)

    def test_cannot_revoke_foundation_admin_as_school_admin(self):
        fadmin_assignment = RoleAssignment.all_tenants.get(user=self.fadmin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN)
        fadmin_staff = Staff.all_tenants.get(user=self.fadmin)
        self.client.force_authenticate(user=self.head_a)
        response = self.client.delete(self.detail_url(fadmin_staff, fadmin_assignment))
        # Out of ceiling target staff -> 404 before the role check even runs.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assignment_must_belong_to_the_url_staff_member(self):
        assignment = self._grant(self.fadmin, self.staff_b, RoleAssignment.ROLE_COUNSELLOR, S, self.school_b.id)
        self.client.force_authenticate(user=self.fadmin)
        response = self.client.delete(self.detail_url(self.staff_a, assignment))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(RoleAssignment.all_tenants.filter(id=assignment.id, deleted_at__isnull=True).exists())

    def test_last_foundation_admin_cannot_be_revoked(self):
        # self.fadmin is the sole foundation admin; a superuser (who is_foundation_admin
        # treats as one, per rbac.is_foundation_admin) tries to remove that last seat.
        fadmin_assignment = RoleAssignment.all_tenants.get(user=self.fadmin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN)
        fadmin_staff = Staff.all_tenants.get(user=self.fadmin)
        superuser = self.make_user('Root', None, F, self.foundation.id, None, with_staff=False)
        User.all_tenants.filter(id=superuser.id).update(is_superuser=True)
        superuser.refresh_from_db()
        self.client.force_authenticate(user=superuser)
        response = self.client.delete(self.detail_url(fadmin_staff, fadmin_assignment))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(RoleAssignment.all_tenants.filter(id=fadmin_assignment.id, deleted_at__isnull=True).exists())

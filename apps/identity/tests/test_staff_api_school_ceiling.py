"""School ceiling on the JSON StaffViewSet: a school-scoped manager may only
see and manage staff inside their own schools, and never someone whose access
reaches beyond them (mirrors the web console, console_access.can_manage_staff)."""
from datetime import date

from rest_framework.test import APITestCase

from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User


def _user(foundation, phone, name, role=None, scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=None):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    if role:
        RoleAssignment.objects.create(
            foundation_id=foundation.id, user=user, role=role, scope_type=scope_type,
            scope_id=foundation.id if scope_id is None else scope_id,
        )
    return user


def _staff(foundation, school, name, phone):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    person = Person.objects.create(foundation_id=foundation.id, full_name=name)
    return Staff.objects.create(
        foundation_id=foundation.id, person=person, user=user, school=school, join_date=date(2026, 1, 1),
    )


class StaffApiSchoolCeilingTests(APITestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(foundation_id=self.foundation.id, name='SMA A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=self.foundation.id, name='SMA B', npsn='22222222', level=School.LEVEL_SMA)
        self.chair = _user(self.foundation, '+6281300003001', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.head_a = _user(
            self.foundation, '+6281300003002', 'Head A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.teacher_a = _staff(self.foundation, self.school_a, 'Budi', '+6281300003003')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher_a.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_a.id,
        )
        self.teacher_b = _staff(self.foundation, self.school_b, 'Sari', '+6281300003004')
        self.boss_a = _staff(self.foundation, self.school_a, 'Boss', '+6281300003005')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.boss_a.user, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )

    def payload(self, **overrides):
        data = {
            'school_id': self.school_a.id, 'join_date': '2026-07-01', 'full_name': 'Dewi Baru',
            'phone_e164': '+6281377770001',
        }
        data.update(overrides)
        return data

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    # --- create ---------------------------------------------------------
    def test_school_admin_creates_in_own_school(self):
        self.as_user(self.head_a)
        self.assertEqual(self.client.post('/api/v1/staff/', self.payload(), format='json').status_code, 201)

    def test_school_admin_cannot_create_in_another_school(self):
        self.as_user(self.head_a)
        response = self.client.post('/api/v1/staff/', self.payload(school_id=self.school_b.id), format='json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Staff.all_tenants.filter(person__full_name='Dewi Baru').exists())

    def test_school_admin_cannot_create_foundation_wide_staff(self):
        self.as_user(self.head_a)
        payload = self.payload()
        del payload['school_id']
        self.assertEqual(self.client.post('/api/v1/staff/', payload, format='json').status_code, 403)

    def test_unknown_school_is_a_404_not_a_500(self):
        self.as_user(self.chair)
        self.assertEqual(self.client.post('/api/v1/staff/', self.payload(school_id=999999), format='json').status_code, 404)

    def test_foundation_admin_creates_anywhere_including_foundation_wide(self):
        self.as_user(self.chair)
        self.assertEqual(self.client.post('/api/v1/staff/', self.payload(school_id=self.school_b.id), format='json').status_code, 201)
        payload = self.payload(phone_e164='+6281377770002')
        del payload['school_id']
        self.assertEqual(self.client.post('/api/v1/staff/', payload, format='json').status_code, 201)

    # --- read -----------------------------------------------------------
    def test_school_admin_lists_and_retrieves_only_own_school_staff(self):
        self.as_user(self.head_a)
        names = {row['person']['full_name'] for row in self.client.get('/api/v1/staff/').data['results']}
        self.assertEqual(names, {'Budi', 'Boss'})
        self.assertEqual(self.client.get(f'/api/v1/staff/{self.teacher_b.id}/').status_code, 404)

    def test_foundation_admin_lists_everyone(self):
        self.as_user(self.chair)
        names = {row['person']['full_name'] for row in self.client.get('/api/v1/staff/').data['results']}
        self.assertEqual(names, {'Budi', 'Sari', 'Boss'})

    # --- update ---------------------------------------------------------
    def test_school_admin_cannot_patch_another_schools_staff(self):
        self.as_user(self.head_a)
        self.assertEqual(self.client.patch(f'/api/v1/staff/{self.teacher_b.id}/', {'nip': 'X'}, format='json').status_code, 404)

    def test_school_admin_cannot_move_staff_out_of_their_schools(self):
        self.as_user(self.head_a)
        response = self.client.patch(f'/api/v1/staff/{self.teacher_a.id}/', {'school': self.school_b.id}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).school_id, self.school_a.id)

    def test_school_admin_can_edit_own_school_staff(self):
        self.as_user(self.head_a)
        response = self.client.patch(f'/api/v1/staff/{self.teacher_a.id}/', {'nip': '1234'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).nip, '1234')

    # --- offboard / destroy ---------------------------------------------
    def test_school_admin_offboards_own_school_staff(self):
        self.as_user(self.head_a)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 200)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_OFFBOARDED)

    def test_school_admin_cannot_offboard_another_schools_staff(self):
        self.as_user(self.head_a)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_b.id}/offboard/', {}, format='json').status_code, 404)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_b.id).status, Staff.STATUS_ACTIVE)

    def test_school_admin_cannot_offboard_or_delete_someone_with_foundation_authority(self):
        self.as_user(self.head_a)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.boss_a.id}/offboard/', {}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/v1/staff/{self.boss_a.id}/').status_code, 404)
        self.assertTrue(User.all_tenants.get(id=self.boss_a.user_id).is_active)
        self.assertTrue(Staff.all_tenants.filter(id=self.boss_a.id, deleted_at__isnull=True).exists())

    def test_cannot_offboard_yourself_via_api(self):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher_a.user, role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_a.id,
        )
        self.as_user(self.teacher_a.user)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 404)

    def test_foundation_admin_offboards_any_school(self):
        self.as_user(self.chair)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_b.id}/offboard/', {}, format='json').status_code, 200)

    def test_teacher_without_write_permission_is_forbidden(self):
        self.as_user(self.teacher_a.user)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 403)

    def test_school_admin_cannot_offboard_someone_who_also_holds_a_role_in_another_school(self):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher_a.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_b.id,
        )
        self.as_user(self.head_a)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 404)

    def test_school_admin_cannot_offboard_a_superuser(self):
        User.all_tenants.filter(id=self.teacher_a.user_id).update(is_superuser=True)
        self.as_user(self.head_a)
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 404)

    def test_repeat_offboard_is_a_400_from_the_service(self):
        self.as_user(self.chair)
        self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json')
        self.assertEqual(self.client.post(f'/api/v1/staff/{self.teacher_a.id}/offboard/', {}, format='json').status_code, 400)

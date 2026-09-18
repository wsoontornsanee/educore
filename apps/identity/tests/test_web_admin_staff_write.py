from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User


def _user(foundation, phone, name, role=None, scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=None):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    if role:
        RoleAssignment.objects.create(
            foundation_id=foundation.id, user=user, role=role, scope_type=scope_type,
            scope_id=scope_id if scope_id is not None else foundation.id,
        )
    return user


def _staff(foundation, school, name, phone, status=Staff.STATUS_ACTIVE):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    person = Person.objects.create(foundation_id=foundation.id, full_name=name)
    return Staff.objects.create(
        foundation_id=foundation.id, person=person, user=user, school=school,
        join_date=timezone.localdate(), status=status,
    )


class StaffWriteTestBase(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(foundation_id=self.foundation.id, name='SMA A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=self.foundation.id, name='SMA B', npsn='22222222', level=School.LEVEL_SMA)
        self.chair = _user(self.foundation, '+6281300002001', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.head_a = _user(
            self.foundation, '+6281300002002', 'Head A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.teacher_a = _staff(self.foundation, self.school_a, 'Budi Guru', '+6281300002003')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher_a.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_a.id,
        )
        self.teacher_b = _staff(self.foundation, self.school_b, 'Sari Guru', '+6281300002004')
        self.create_url = reverse('admin-staff-create')

    def payload(self, **overrides):
        data = {
            'full_name': 'Dewi Baru', 'phone_e164': '081300009001', 'email': 'dewi@example.sch.id', 'nip': '1990',
            'employment_type': 'CONTRACT', 'join_date': '2026-07-01', 'school': str(self.school_a.id),
        }
        data.update(overrides)
        return data

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class StaffCreateViewTests(StaffWriteTestBase):
    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.post(self.create_url, self.payload()).status_code, 302)
        self.assertFalse(Staff.all_tenants.filter(person__full_name='Dewi Baru').exists())

    def test_user_without_write_permission_redirected_home_and_nothing_created(self):
        self.client.force_login(self.teacher_a.user)
        response = self.client.post(self.create_url, self.payload())
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertFalse(Staff.all_tenants.filter(person__full_name='Dewi Baru').exists())

    def test_school_admin_creates_staff_in_own_school(self):
        self.client.force_login(self.head_a)
        response = self.client.post(self.create_url, self.payload())
        self.assertRedirects(response, reverse('admin-staff'), fetch_redirect_response=False)
        staff = Staff.all_tenants.get(person__full_name='Dewi Baru')
        self.assertEqual(staff.school_id, self.school_a.id)
        self.assertEqual(staff.user.phone_e164, '+6281300009001')
        self.assertEqual(staff.status, Staff.STATUS_ACTIVE)
        self.assertTrue(AuditEvent.objects.filter(action='identity.staff.created', entity_id=str(staff.id)).exists())

    def test_school_admin_cannot_create_in_another_school(self):
        self.client.force_login(self.head_a)
        response = self.client.post(self.create_url, self.payload(school=str(self.school_b.id)))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Staff.all_tenants.filter(person__full_name='Dewi Baru').exists())

    def test_school_admin_cannot_create_foundation_wide_staff(self):
        self.client.force_login(self.head_a)
        response = self.client.post(self.create_url, self.payload(school=''))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Staff.all_tenants.filter(person__full_name='Dewi Baru').exists())

    def test_foundation_admin_may_create_foundation_wide_staff(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.create_url, self.payload(school=''))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Staff.all_tenants.get(person__full_name='Dewi Baru').school_id)

    def test_duplicate_phone_or_email_is_a_form_error_not_a_crash(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.create_url, self.payload(phone_e164='+6281300002003'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'sudah terdaftar', status_code=400)
        User.all_tenants.filter(phone_e164='+6281300002003').update(email='taken@example.sch.id')
        response = self.client.post(self.create_url, self.payload(email='TAKEN@example.sch.id'))
        self.assertEqual(response.status_code, 400)

    def test_invalid_phone_rejected(self):
        self.client.force_login(self.chair)
        self.assertEqual(self.client.post(self.create_url, self.payload(phone_e164='12345')).status_code, 400)

    def test_get_renders_form_offering_only_permitted_schools(self):
        self.client.force_login(self.head_a)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SMA A')
        self.assertNotContains(response, 'SMA B')


class StaffOffboardViewTests(StaffWriteTestBase):
    def url(self, staff):
        return reverse('admin-staff-offboard', args=[staff.id])

    def test_foundation_admin_offboards_staff(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url(self.teacher_a), {'reason': 'Pindah kota'})
        self.assertRedirects(response, reverse('admin-staff'), fetch_redirect_response=False)
        staff = Staff.all_tenants.get(id=self.teacher_a.id)
        user = User.all_tenants.get(id=self.teacher_a.user_id)
        self.assertEqual(staff.status, Staff.STATUS_OFFBOARDED)
        self.assertEqual(staff.resignation_reason, 'Pindah kota')
        self.assertFalse(user.is_active)
        self.assertFalse(RoleAssignment.objects.filter(user=user, deleted_at__isnull=True).exists())
        self.assertTrue(AuditEvent.objects.filter(action='identity.staff.offboarded', entity_id=str(staff.id)).exists())

    def test_school_admin_offboards_staff_in_own_school(self):
        self.client.force_login(self.head_a)
        self.client.post(self.url(self.teacher_a))
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_OFFBOARDED)

    def test_school_admin_cannot_offboard_staff_of_another_school(self):
        self.client.force_login(self.head_a)
        self.assertEqual(self.client.post(self.url(self.teacher_b)).status_code, 404)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_b.id).status, Staff.STATUS_ACTIVE)

    def test_school_admin_cannot_offboard_someone_holding_foundation_authority(self):
        """A staff row in school A whose user is also a foundation admin: the
        school head must not be able to revoke that foundation-wide access."""
        boss = _staff(self.foundation, self.school_a, 'Boss', '+6281300002005')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=boss.user, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.client.force_login(self.head_a)
        self.assertEqual(self.client.post(self.url(boss)).status_code, 404)
        self.assertTrue(User.all_tenants.get(id=boss.user_id).is_active)

    def test_school_admin_cannot_offboard_someone_with_a_role_in_another_school_too(self):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher_a.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_b.id,
        )
        self.client.force_login(self.head_a)
        self.assertEqual(self.client.post(self.url(self.teacher_a)).status_code, 404)

    def test_cannot_offboard_yourself(self):
        me = _staff(self.foundation, None, 'Chair Staff', '+6281300002006')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=me.user, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.client.force_login(me.user)
        self.assertEqual(self.client.post(self.url(me)).status_code, 404)
        self.assertEqual(Staff.all_tenants.get(id=me.id).status, Staff.STATUS_ACTIVE)

    def test_user_without_write_permission_cannot_offboard(self):
        self.client.force_login(self.teacher_b.user)
        response = self.client.post(self.url(self.teacher_a))
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_ACTIVE)

    def test_other_foundations_staff_is_a_404(self):
        other = Foundation.objects.create(legal_name='Other', brand_name='Other')
        other_school = School.objects.create(foundation_id=other.id, name='O', npsn='33333333', level=School.LEVEL_SMA)
        outsider = _staff(other, other_school, 'Outsider', '+6281300002099')
        self.client.force_login(self.chair)
        self.assertEqual(self.client.post(self.url(outsider)).status_code, 404)
        self.assertEqual(Staff.all_tenants.get(id=outsider.id).status, Staff.STATUS_ACTIVE)

    def test_already_offboarded_reports_an_error_and_changes_nothing(self):
        Staff.all_tenants.filter(id=self.teacher_a.id).update(status=Staff.STATUS_OFFBOARDED)
        self.client.force_login(self.chair)
        response = self.client.post(self.url(self.teacher_a))
        self.assertRedirects(response, reverse('admin-staff'), fetch_redirect_response=False)
        self.assertTrue(self.messages(response))

    def test_get_is_not_allowed(self):
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(self.url(self.teacher_a)).status_code, 405)

    def test_reassign_target_must_be_an_active_colleague_in_the_same_school(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url(self.teacher_a), {'reassign_to_staff_id': str(self.teacher_b.id)})
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_ACTIVE)
        self.assertTrue(self.messages(response))
        colleague = _staff(self.foundation, self.school_a, 'Rekan', '+6281300002007')
        self.client.post(self.url(self.teacher_a), {'reassign_to_staff_id': str(colleague.id)})
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_OFFBOARDED)

    def test_bad_resignation_date_reports_an_error(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.url(self.teacher_a), {'resignation_date': 'not-a-date'})
        self.assertEqual(Staff.all_tenants.get(id=self.teacher_a.id).status, Staff.STATUS_ACTIVE)
        self.assertTrue(self.messages(response))


class StaffDirectoryActionsTests(StaffWriteTestBase):
    def test_write_holder_sees_add_button_and_offboard_only_for_manageable_rows(self):
        self.client.force_login(self.head_a)
        response = self.client.get(reverse('admin-staff'))
        self.assertContains(response, self.create_url)
        rows = {row.person.full_name: row.can_offboard for row in response.context['staff_rows']}
        self.assertEqual(rows, {'Budi Guru': True})


class StaffWriteEnglishTests(StaffWriteTestBase):
    def test_add_staff_page_translates(self):
        self.client.force_login(self.chair)
        self.client.cookies['django_language'] = 'en'
        response = self.client.get(reverse('admin-staff'))
        self.assertContains(response, 'Add staff')

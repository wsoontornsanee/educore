"""Administrasi > Staf & jabatan write actions: add staff, offboard staff."""
import datetime

from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def _role_user(foundation, phone, name, role, scope_type, scope_id):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    RoleAssignment.objects.create(foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id)
    return user


def _staff(foundation, school, name, phone, status=Staff.STATUS_ACTIVE, user=None):
    user = user or User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    person = Person.objects.create(foundation_id=foundation.id, full_name=name)
    return Staff.objects.create(
        foundation_id=foundation.id, person=person, user=user, school=school,
        join_date=timezone.localdate(), status=status,
    )


class StaffActionTestBase(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(foundation_id=self.foundation.id, name='SMA A', npsn='31111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=self.foundation.id, name='SMA B', npsn='32222222', level=School.LEVEL_SMA)
        self.chair = _role_user(
            self.foundation, '+6281400001001', 'Ketua', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        self.admin_a = _role_user(
            self.foundation, '+6281400001002', 'Admin A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.teacher_user = _role_user(
            self.foundation, '+6281400001003', 'Guru', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.create_url = reverse('admin-staff-create')

    def tearDown(self):
        clear_current_foundation_id()

    def offboard_url(self, staff):
        return reverse('admin-staff-offboard', kwargs={'staff_id': staff.id})

    def valid_payload(self, **overrides):
        payload = {
            'full_name': 'Rina Guru', 'phone_e164': '081234567890', 'email': 'rina@sekolah.test',
            'school': self.school_a.id, 'nip': '1990', 'employment_type': 'PERMANENT',
            'join_date': '2026-07-01',
        }
        payload.update(overrides)
        return payload


class StaffCreateTests(StaffActionTestBase):
    def test_school_admin_creates_staff_in_their_school(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(password=''))
        self.assertRedirects(response, reverse('admin-staff'))
        staff = Staff.all_tenants.get(nip='1990')
        self.assertEqual(staff.school_id, self.school_a.id)
        self.assertEqual(staff.person.full_name, 'Rina Guru')
        self.assertEqual(staff.user.phone_e164, '+6281234567890')  # normalized like the API
        self.assertEqual(staff.status, Staff.STATUS_ACTIVE)
        self.assertTrue(AuditEvent.objects.filter(action='identity.staff.created', entity_id=str(staff.id)).exists())

    def test_success_message_shown_on_directory(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(), follow=True)
        self.assertContains(response, 'Rina Guru ditambahkan.')

    def test_school_admin_cannot_create_into_another_school(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(school=self.school_b.id))
        self.assertEqual(response.status_code, 200)
        self.assertIn('school', response.context['form'].errors)
        self.assertFalse(Staff.all_tenants.filter(nip='1990').exists())

    def test_school_admin_must_pick_a_school(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(school=''))
        self.assertIn('school', response.context['form'].errors)

    def test_foundation_admin_may_create_foundation_level_staff(self):
        self.client.force_login(self.chair)
        response = self.client.post(self.create_url, self.valid_payload(school=''))
        self.assertRedirects(response, reverse('admin-staff'))
        self.assertIsNone(Staff.all_tenants.get(nip='1990').school_id)

    def test_duplicate_phone_and_email_are_field_errors_not_500s(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(phone_e164='081400001003', email=self.teacher_user.email or 'x@x.test'))
        self.assertIn('phone_e164', response.context['form'].errors)
        User.all_tenants.filter(pk=self.teacher_user.pk).update(email='dup@sekolah.test')
        response = self.client.post(self.create_url, self.valid_payload(email='DUP@sekolah.test'))
        self.assertIn('email', response.context['form'].errors)

    def test_invalid_phone_nik_and_weak_password_are_field_errors(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(self.create_url, self.valid_payload(phone_e164='12345', nik='123', password='123'))
        errors = response.context['form'].errors
        self.assertIn('phone_e164', errors)
        self.assertIn('nik', errors)
        self.assertIn('password', errors)
        self.assertFalse(Staff.all_tenants.filter(nip='1990').exists())

    def test_teacher_without_write_permission_is_bounced(self):
        self.client.force_login(self.teacher_user)
        self.assertRedirects(self.client.get(self.create_url), reverse('web-console-home'), fetch_redirect_response=False)
        self.client.post(self.create_url, self.valid_payload())
        self.assertFalse(Staff.all_tenants.filter(nip='1990').exists())

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])


class StaffOffboardTests(StaffActionTestBase):
    def setUp(self):
        super().setUp()
        self.target = _staff(self.foundation, self.school_a, 'Budi', '+6281400002001')
        self.colleague = _staff(self.foundation, self.school_a, 'Sari', '+6281400002002')
        self.other_school_staff = _staff(self.foundation, self.school_b, 'Tono', '+6281400002003')

    def _post(self, staff, **extra):
        data = {'resignation_date': '2026-09-01', 'reason': 'Pindah'}
        data.update(extra)
        return self.client.post(self.offboard_url(staff), data)

    def test_school_admin_offboards_staff_of_their_school(self):
        session = SessionStore()
        session['_auth_user_id'] = str(self.target.user_id)
        session.create()
        self.client.force_login(self.admin_a)
        response = self._post(self.target, reassign_to=self.colleague.id)
        self.assertRedirects(response, reverse('admin-staff'))
        self.target.refresh_from_db()
        self.target.user.refresh_from_db()
        self.assertEqual(self.target.status, Staff.STATUS_OFFBOARDED)
        self.assertEqual(self.target.resignation_date, datetime.date(2026, 9, 1))
        self.assertEqual(self.target.resignation_reason, 'Pindah')
        self.assertFalse(self.target.user.is_active)
        self.assertTrue(AuditEvent.objects.filter(action='identity.staff.offboarded', entity_id=str(self.target.id)).exists())

    def test_offboarding_revokes_the_targets_roles(self):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.target.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_a.id,
        )
        self.client.force_login(self.admin_a)
        self._post(self.target)
        self.assertFalse(RoleAssignment.all_tenants.filter(user=self.target.user, deleted_at__isnull=True).exists())

    def test_get_shows_confirmation_with_same_school_reassign_choices_only(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(self.offboard_url(self.target))
        self.assertContains(response, 'Offboard Budi')
        choices = {name for _value, name in response.context['form'].fields['reassign_to'].choices}
        self.assertIn('Sari', choices)
        self.assertNotIn('Tono', choices)  # other school
        self.assertNotIn('Budi', choices)  # the person leaving

    def test_school_admin_cannot_offboard_another_schools_staff(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(self.offboard_url(self.other_school_staff)).status_code, 404)
        self.assertEqual(self._post(self.other_school_staff).status_code, 404)
        self.other_school_staff.refresh_from_db()
        self.assertEqual(self.other_school_staff.status, Staff.STATUS_ACTIVE)

    def test_school_admin_cannot_offboard_a_foundation_administrator(self):
        chair_staff = _staff(self.foundation, self.school_a, 'Ketua Staf', '+6281400002004', user=self.chair)
        self.client.force_login(self.admin_a)
        response = self._post(chair_staff, follow=False)
        self.assertRedirects(response, reverse('admin-staff'))
        chair_staff.refresh_from_db()
        self.assertEqual(chair_staff.status, Staff.STATUS_ACTIVE)
        self.assertTrue(RoleAssignment.all_tenants.filter(user=self.chair, deleted_at__isnull=True).exists())

    def test_foundation_admin_can_offboard_any_school_and_foundation_level_staff(self):
        floating = _staff(self.foundation, None, 'Tanpa Sekolah', '+6281400002005')
        self.client.force_login(self.chair)
        self._post(self.other_school_staff)
        self._post(floating)
        self.other_school_staff.refresh_from_db()
        floating.refresh_from_db()
        self.assertEqual(self.other_school_staff.status, Staff.STATUS_OFFBOARDED)
        self.assertEqual(floating.status, Staff.STATUS_OFFBOARDED)

    def test_cannot_offboard_yourself(self):
        own = _staff(self.foundation, self.school_a, 'Admin A Staf', '+6281400002006', user=self.admin_a)
        self.client.force_login(self.admin_a)
        response = self._post(own)
        self.assertRedirects(response, reverse('admin-staff'))
        own.refresh_from_db()
        self.assertEqual(own.status, Staff.STATUS_ACTIVE)

    def test_already_offboarded_staff_is_refused(self):
        gone = _staff(self.foundation, self.school_a, 'Lama', '+6281400002007', status=Staff.STATUS_OFFBOARDED)
        self.client.force_login(self.admin_a)
        self.assertRedirects(self.client.get(self.offboard_url(gone)), reverse('admin-staff'))

    def test_other_tenants_staff_is_404(self):
        other = Foundation.objects.create(legal_name='G', brand_name='G')
        other_school = School.objects.create(foundation_id=other.id, name='SMA Z', npsn='39999999', level=School.LEVEL_SMA)
        foreign = _staff(other, other_school, 'Asing', '+6281400009999')
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(self.offboard_url(foreign)).status_code, 404)

    def test_teacher_without_write_permission_is_bounced(self):
        self.client.force_login(self.teacher_user)
        self.assertRedirects(self._post(self.target), reverse('web-console-home'), fetch_redirect_response=False)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, Staff.STATUS_ACTIVE)

    def test_get_does_not_mutate(self):
        self.client.force_login(self.admin_a)
        self.client.get(self.offboard_url(self.target))
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, Staff.STATUS_ACTIVE)


class StaffDirectoryActionLinksTests(StaffActionTestBase):
    def setUp(self):
        super().setUp()
        self.target = _staff(self.foundation, self.school_a, 'Budi', '+6281400003001')
        self.other = _staff(self.foundation, self.school_b, 'Tono', '+6281400003002')

    def test_school_admin_sees_add_and_offboard_only_for_their_own_school(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse('admin-staff'))
        self.assertContains(response, self.create_url)
        self.assertContains(response, self.offboard_url(self.target))
        self.assertNotContains(response, self.offboard_url(self.other))

    def test_foundation_admin_sees_offboard_for_every_active_row(self):
        self.client.force_login(self.chair)
        response = self.client.get(reverse('admin-staff'))
        self.assertContains(response, self.offboard_url(self.target))
        self.assertContains(response, self.offboard_url(self.other))

    def test_english_labels(self):
        self.client.force_login(self.admin_a)
        self.client.cookies['django_language'] = 'en'
        try:
            self.assertContains(self.client.get(reverse('admin-staff')), 'Add staff')
        finally:
            from django.utils import translation
            translation.deactivate()

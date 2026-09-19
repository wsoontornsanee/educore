from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User


def _make_user(foundation, phone, name, role, scope_type, scope_id):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    RoleAssignment.objects.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    return user


def _make_staff(foundation, school, name, phone, nip=None, status=Staff.STATUS_ACTIVE):
    user = User.objects.create(phone_e164=phone, full_name=name, foundation_id=foundation.id)
    person = Person.objects.create(foundation_id=foundation.id, full_name=name)
    return Staff.objects.create(
        foundation_id=foundation.id, person=person, user=user, school=school, nip=nip,
        join_date=timezone.localdate(), status=status,
    )


class StaffDirectoryViewTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(foundation_id=self.foundation.id, name='SMA A', npsn='11111111', level=School.LEVEL_SMA)
        self.school_b = School.objects.create(foundation_id=self.foundation.id, name='SMA B', npsn='22222222', level=School.LEVEL_SMA)
        self.admin = _make_user(
            self.foundation, '+6281300001001', 'Chair', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        self.staff_a = _make_staff(self.foundation, self.school_a, 'Budi Guru', '+6281300001002', nip='1980')
        self.staff_b = _make_staff(self.foundation, self.school_b, 'Sari Guru', '+6281300001003')
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.staff_a.user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school_a.id,
        )
        self.url = reverse('admin-staff')

    def _rows(self, response):
        return {row.person.full_name for row in response.context['staff_rows']}

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_foundation_admin_sees_all_staff_with_roles(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), {'Budi Guru', 'Sari Guru'})
        budi = next(row for row in response.context['staff_rows'] if row.person.full_name == 'Budi Guru')
        self.assertEqual(budi.role_list, [{'label': 'Guru', 'scope': 'SMA A'}])
        self.assertContains(response, 'Budi Guru')

    def test_user_without_school_config_read_redirected_home(self):
        self.client.force_login(self.staff_a.user)  # teacher: no school_config.read
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)

    def test_school_admin_sees_only_own_school_staff(self):
        school_admin = _make_user(
            self.foundation, '+6281300001004', 'Head A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.client.force_login(school_admin)
        response = self.client.get(self.url)
        self.assertEqual(self._rows(response), {'Budi Guru'})
        self.assertEqual([school.name for school in response.context['schools']], ['SMA A'])

    def test_school_admin_school_filter_cannot_widen_scope(self):
        school_admin = _make_user(
            self.foundation, '+6281300001004', 'Head A', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school_a.id,
        )
        self.client.force_login(school_admin)
        response = self.client.get(self.url, {'school': self.school_b.id})
        self.assertEqual(self._rows(response), set())

    def test_other_foundation_staff_never_listed(self):
        other = Foundation.objects.create(legal_name='Other', brand_name='Other')
        other_school = School.objects.create(foundation_id=other.id, name='Other S', npsn='33333333', level=School.LEVEL_SMA)
        _make_staff(other, other_school, 'Outsider', '+6281300001099')
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertNotIn('Outsider', self._rows(response))

    def test_search_by_name_and_nip(self):
        self.client.force_login(self.admin)
        self.assertEqual(self._rows(self.client.get(self.url, {'q': 'sari'})), {'Sari Guru'})
        self.assertEqual(self._rows(self.client.get(self.url, {'q': '1980'})), {'Budi Guru'})

    def test_status_filter_and_invalid_status_ignored(self):
        Staff.all_tenants.filter(id=self.staff_b.id).update(status=Staff.STATUS_OFFBOARDED)
        self.client.force_login(self.admin)
        self.assertEqual(
            self._rows(self.client.get(self.url, {'status': Staff.STATUS_OFFBOARDED})), {'Sari Guru'},
        )
        response = self.client.get(self.url, {'status': 'bogus'})
        self.assertEqual(self._rows(response), {'Budi Guru', 'Sari Guru'})
        self.assertEqual(response.context['filters']['status'], '')

    def test_nav_links_to_staff_page_for_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, f'href="{self.url}"')

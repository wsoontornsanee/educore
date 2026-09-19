from django.test import TestCase
from django.urls import reverse

from apps.identity.models import Foundation, PlatformRoleAssignment, RoleAssignment, School, User


class HeaderRolesTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.school2 = School.objects.create(foundation_id=self.foundation.id, name='S2', npsn='12345679', level=School.LEVEL_SMA)
        self.user = User.objects.create(phone_e164='+6281300000077', full_name='Multi Role', foundation_id=self.foundation.id)
        self.user.set_password('pw12345')
        self.user.save()

    def _grant(self, role, school):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=role,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
        )

    def _header_roles(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('console:coming_soon'))
        return response.context['header_roles']

    def test_shows_role_label_in_header(self):
        self._grant(RoleAssignment.ROLE_TEACHER, self.school)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('console:coming_soon')), 'data-testid="header-roles">Guru<')

    def test_same_role_at_two_schools_reads_once_and_roles_join(self):
        self._grant(RoleAssignment.ROLE_TEACHER, self.school)
        self._grant(RoleAssignment.ROLE_TEACHER, self.school2)
        self._grant(RoleAssignment.ROLE_SCHOOL_ADMIN, self.school)
        self.assertEqual(self._header_roles(), ['Admin Sekolah', 'Guru'])

    def test_platform_role_included(self):
        PlatformRoleAssignment.objects.create(user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.assertEqual(self._header_roles(), ['Operator Platform'])

    def test_anonymous_login_page_has_no_role(self):
        response = self.client.get(reverse('web-login'))
        self.assertNotContains(response, 'data-testid="header-roles"')

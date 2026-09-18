from django.test import TestCase
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.landing import resolve_post_login_redirect


class ResolvePostLoginRedirectTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)

    def _assign(self, user, role):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=role,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )

    def test_foundation_admin_lands_on_overview(self):
        user = User.objects.create(phone_e164='+6281300000010', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/overview/')

    def test_higher_priority_role_wins_when_user_holds_two(self):
        user = User.objects.create(phone_e164='+6281300000011', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_TEACHER)
        self._assign(user, RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/overview/')

    def test_teacher_outranks_finance_officer(self):
        user = User.objects.create(phone_e164='+6281300000014', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_FINANCE_OFFICER)
        self._assign(user, RoleAssignment.ROLE_TEACHER)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/agenda/')

    def test_canteen_operator_lands_on_console_home(self):
        user = User.objects.create(phone_e164='+6281300000012', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_CANTEEN_OPERATOR)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/')

    def test_no_role_assignment_falls_back_to_grades_read_check(self):
        user = User.objects.create(phone_e164='+6281300000013', full_name='U', foundation_id=self.foundation.id)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/')

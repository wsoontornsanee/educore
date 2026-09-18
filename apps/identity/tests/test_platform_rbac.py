from django.test import TestCase
from apps.identity.models import PlatformRoleAssignment, User
from apps.identity.rbac import has_platform_permission, get_platform_permissions


class PlatformRbacTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create(phone_e164='+6281200000002', full_name='Ops Two')
        self.plain_user = User.objects.create(phone_e164='+6281200000003', full_name='Plain User')
        self.superuser = User.objects.create(
            phone_e164='+6281200000004', full_name='Super User', is_superuser=True,
        )
        PlatformRoleAssignment.objects.create(
            user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )

    def test_operator_has_status_write(self):
        self.assertTrue(has_platform_permission(self.operator, 'status.write'))

    def test_plain_user_lacks_status_write(self):
        self.assertFalse(has_platform_permission(self.plain_user, 'status.write'))

    def test_unknown_permission_key_denied_even_for_operator(self):
        self.assertFalse(has_platform_permission(self.operator, 'not.a.real.permission'))

    def test_superuser_has_all_platform_permissions(self):
        self.assertTrue(has_platform_permission(self.superuser, 'status.write'))
        self.assertIn('status.write', get_platform_permissions(self.superuser))

from django.db import IntegrityError
from django.test import TestCase
from apps.identity.models import PlatformRoleAssignment, User, Foundation


class PlatformRoleAssignmentTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name='Test Foundation',
            brand_name='Test Foundation',
        )
        self.user = User.objects.create(
            phone_e164='+6281200000001',
            full_name='Ops One',
            foundation_id=self.foundation.id,
        )

    def test_create_platform_operator_assignment(self):
        assignment = PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        self.assertEqual(assignment.role, 'platform_operator')
        self.assertIsNotNone(assignment.created_at)

    def test_duplicate_user_role_rejected(self):
        PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        with self.assertRaises(IntegrityError):
            PlatformRoleAssignment.objects.create(
                user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
            )

    def test_str_representation(self):
        assignment = PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        self.assertIn('platform_operator', str(assignment))
        self.assertIn('PLATFORM', str(assignment))

    def test_user_access_via_select_related_without_tenant_context(self):
        """Regression test: forward access to .user on a freshly-loaded PlatformRoleAssignment
        with select_related succeeds even outside tenant context.

        Without select_related, accessing .user on a freshly-loaded instance outside a tenant
        context would return None (because User.objects uses TenantManager, which fails closed
        with no tenant context). With select_related, the JOIN is performed in
        PlatformRoleAssignment's plain queryset, bypassing the manager entirely.
        """
        assignment = PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )

        # Fetch a FRESH instance (not the cached in-memory object) via select_related
        fresh_assignment = PlatformRoleAssignment.objects.select_related('user').get(pk=assignment.pk)

        # Access to .user should succeed and return the correct user, proving select_related worked
        self.assertEqual(fresh_assignment.user.phone_e164, '+6281200000001')
        self.assertEqual(fresh_assignment.user.full_name, 'Ops One')

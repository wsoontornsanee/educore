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

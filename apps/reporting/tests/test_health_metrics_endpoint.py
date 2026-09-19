import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import PlatformRoleAssignment, RoleAssignment, User
from apps.identity.rbac import has_platform_permission
from apps.reporting.models import RptParentWeeklyActivity

URL = '/api/v1/internal/health-metrics/'


class HealthMetricsEndpointTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.operator = User.all_tenants.create_user(
            phone_e164='+6281200000701', full_name='Ops', foundation_id=self.fid,
        )
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.admin = User.all_tenants.create_user(
            phone_e164='+6281200000702', full_name='Admin Yayasan', foundation_id=self.fid,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fid, user=self.admin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.fid,
        )
        week = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday())
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=week - datetime.timedelta(weeks=1),
            active_parents=4, enrolled_students=10, computed_at=timezone.now(),
        )
        self.client = APIClient()

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')

    def test_operator_gets_the_per_school_series(self):
        self._auth(self.operator)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        (school,) = response.json()['schools']
        self.assertEqual(school['school_id'], self.fx['school'].id)
        self.assertEqual(school['latest_wau_pct'], 40.0)
        self.assertFalse(school['at_risk'])

    def test_foundation_filter(self):
        self._auth(self.operator)
        self.assertEqual(self.client.get(URL, {'foundation_id': self.fid + 999}).json(), {'schools': []})
        self.assertEqual(len(self.client.get(URL, {'foundation_id': self.fid}).json()['schools']), 1)

    def test_a_non_numeric_foundation_id_is_a_400(self):
        self._auth(self.operator)
        self.assertEqual(self.client.get(URL, {'foundation_id': 'abc'}).status_code, 400)

    def test_foundation_admin_is_forbidden(self):
        self._auth(self.admin)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_anonymous_is_unauthorized(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_platform_operator_holds_the_permission_and_plain_users_do_not(self):
        self.assertTrue(has_platform_permission(self.operator, 'platform.health.read'))
        self.assertFalse(has_platform_permission(self.admin, 'platform.health.read'))

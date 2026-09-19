"""Tests for Feature Entitlements gating and User Profile API (spec/02 §6, §7, spec/03 §3)."""
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIRequestFactory
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.identity.models import Foundation, School, User, RoleAssignment
from apps.identity.entitlements import (
    is_module_entitled, get_active_entitlements, set_module_entitlement,
    MODULE_WALLET, MODULE_PAYROLL, MODULE_FINANCE, ALL_MODULES
)
from apps.identity.permissions import RequiresModuleEntitlement, ModuleNotEntitled
from apps.identity.rbac import assign_role, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, SCOPE_SCHOOL
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class EntitlementTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cita",
            brand_name="Insan Cita",
        )
        self.school_1 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Insan Cita 1",
            npsn="50100001",
            level=School.LEVEL_SD,
        )
        self.school_2 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Insan Cita 2",
            npsn="50100002",
            level=School.LEVEL_SMP,
        )

        self.admin = User.all_tenants.create_user(
            phone_e164="+6281122334455",
            foundation_id=self.foundation.id,
            full_name="Admin Yayasan",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        self.school_admin = User.all_tenants.create_user(
            phone_e164="+6282233445566",
            foundation_id=self.foundation.id,
            full_name="Kepala SD",
        )
        assign_role(
            user=self.school_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
            foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_entitlement_hierarchy_and_overrides(self):
        """Verify foundation default vs per-school override resolution (IAM-023, FND-012)."""
        # Default before any record is created: True
        self.assertTrue(is_module_entitled(self.foundation.id, MODULE_WALLET))

        # Disable wallet foundation-wide
        set_module_entitlement(
            foundation_id=self.foundation.id,
            module_key=MODULE_WALLET,
            enabled=False,
        )
        self.assertFalse(is_module_entitled(self.foundation.id, MODULE_WALLET))
        self.assertFalse(is_module_entitled(self.foundation.id, MODULE_WALLET, school_id=self.school_1.id))

        # Override: enable wallet specifically for School 1
        set_module_entitlement(
            foundation_id=self.foundation.id,
            module_key=MODULE_WALLET,
            enabled=True,
            school_id=self.school_1.id,
        )
        # School 1 has it enabled
        self.assertTrue(is_module_entitled(self.foundation.id, MODULE_WALLET, school_id=self.school_1.id))
        # School 2 inherits the disabled foundation default
        self.assertFalse(is_module_entitled(self.foundation.id, MODULE_WALLET, school_id=self.school_2.id))

    def test_drf_module_entitlement_gating_iam_024(self):
        """A disabled module raises ModuleNotEntitled (403 MODULE_NOT_ENTITLED) per IAM-024."""
        factory = APIRequestFactory()
        perm = RequiresModuleEntitlement()

        class WalletView(APIView):
            required_module = MODULE_WALLET

        # Disable wallet for foundation
        set_module_entitlement(self.foundation.id, MODULE_WALLET, enabled=False)

        request = factory.get('/api/v1/wallet/balance')
        request.user = self.admin

        with tenant_context(self.foundation.id):
            with self.assertRaises(ModuleNotEntitled) as ctx:
                perm.has_permission(request, WalletView())
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.get_codes(), 'MODULE_NOT_ENTITLED')

            # Re-enable wallet
            set_module_entitlement(self.foundation.id, MODULE_WALLET, enabled=True)
            self.assertTrue(perm.has_permission(request, WalletView()))

    def test_get_current_user_profile_me(self):
        """Verify GET /api/v1/me returns user, roles, schools, and entitlements (spec/02 §7)."""
        # Test as School Admin
        self.client.force_authenticate(user=self.school_admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/me')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()

            # User details
            self.assertEqual(data['user']['phone_e164'], "+6282233445566")
            self.assertEqual(data['user']['full_name'], "Kepala SD")

            # Roles
            roles = data['roles']
            self.assertEqual(len(roles), 1)
            self.assertEqual(roles[0]['role'], ROLE_SCHOOL_ADMIN)

            # Schools: School admin sees only assigned school (School 1)
            schools = data['schools']
            self.assertEqual(len(schools), 1)
            self.assertEqual(schools[0]['id'], self.school_1.id)

            # Entitlements
            entitlements = data['entitlements']
            for m in ALL_MODULES:
                self.assertIn(m, entitlements)

        # Test as Foundation Admin: sees all schools
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/me')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.json()['schools']), 2)

    def test_foundation_entitlement_viewset(self):
        """Foundation admin can list and update module entitlements via API."""
        self.client.force_authenticate(user=self.admin)

        with tenant_context(self.foundation.id):
            # Create entitlement via API
            create_res = self.client.post('/api/v1/foundation/entitlements/', data={
                'module_key': MODULE_PAYROLL,
                'enabled': False,
            })
            self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
            ent_id = create_res.data['id']
            self.assertFalse(create_res.data['enabled'])

            # Update entitlement to True
            patch_res = self.client.patch(f'/api/v1/foundation/entitlements/{ent_id}/', data={
                'enabled': True,
            })
            self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
            self.assertTrue(patch_res.data['enabled'])

    def test_non_admin_denied_entitlement_management(self):
        """School admin cannot manage foundation entitlements."""
        self.client.force_authenticate(user=self.school_admin)

        with tenant_context(self.foundation.id):
            response = self.client.get('/api/v1/foundation/entitlements/')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class EntitlementHistoryTests(APITestCase):
    """Every change to an entitlement's effective value is appended to EntitlementChange (RPT-010)."""

    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Riwayat", brand_name="Riwayat")
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Riwayat", npsn="50100077", level=School.LEVEL_SD,
        )

    def history(self, school=None):
        from apps.identity.models import EntitlementChange
        return list(EntitlementChange.all_tenants.filter(
            foundation_id=self.foundation.id, school_id=school.id if school else None, module_key=MODULE_WALLET,
        ).order_by('id').values_list('enabled', flat=True))

    def test_setter_logs_creation_and_each_flip_but_not_a_repeat(self):
        set_module_entitlement(self.foundation.id, MODULE_WALLET, True)
        set_module_entitlement(self.foundation.id, MODULE_WALLET, True, limits={'seats': 5})  # limits only
        set_module_entitlement(self.foundation.id, MODULE_WALLET, False)
        set_module_entitlement(self.foundation.id, MODULE_WALLET, True)
        self.assertEqual(self.history(), [True, False, True])

    def test_school_scope_is_logged_separately(self):
        set_module_entitlement(self.foundation.id, MODULE_WALLET, False, school_id=self.school.id)
        self.assertEqual(self.history(), [])
        self.assertEqual(self.history(self.school), [False])

    def test_soft_delete_logs_a_removal_and_restore_logs_the_value_again(self):
        entitlement = set_module_entitlement(self.foundation.id, MODULE_WALLET, False)
        entitlement.delete()
        entitlement.restore()
        self.assertEqual(self.history(), [False, None, False])

    def test_history_row_is_dated_now_and_belongs_to_the_foundation(self):
        from apps.identity.models import EntitlementChange
        set_module_entitlement(self.foundation.id, MODULE_WALLET, False)
        change = EntitlementChange.all_tenants.get(foundation_id=self.foundation.id)
        self.assertLess((timezone.now() - change.effective_from).total_seconds(), 60)

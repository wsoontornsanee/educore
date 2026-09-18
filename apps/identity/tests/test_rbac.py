"""Unit and integration tests for EduCore RBAC and Permission Enforcement (spec/02 §4)."""
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory
from apps.identity.models import Foundation, School, User, RoleAssignment
from apps.identity.rbac import (
    assign_role, revoke_role, has_permission, get_user_permissions,
    ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_FINANCE_OFFICER,
    ROLE_TEACHER, ROLE_COUNSELLOR, ROLE_PARENT,
    SCOPE_FOUNDATION, SCOPE_SCHOOL
)
from apps.identity.permissions import HasRequiredPermission, IsFoundationAdmin
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context

class RBACTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Darul Ulum",
            brand_name="Darul Ulum",
        )
        self.school_1 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Darul Ulum 1",
            npsn="30100001",
            level=School.LEVEL_SMA,
        )
        self.school_2 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Darul Ulum 2",
            npsn="30100002",
            level=School.LEVEL_SMA,
        )

        self.user_foundation_admin = User.all_tenants.create_user(
            phone_e164="+6281111111111",
            foundation_id=self.foundation.id,
            full_name="Ustadz Mansur (Admin Yayasan)",
        )
        self.user_school_admin = User.all_tenants.create_user(
            phone_e164="+6282222222222",
            foundation_id=self.foundation.id,
            full_name="Budi Hartono (Kepala Sekolah SMA 1)",
        )
        self.user_teacher = User.all_tenants.create_user(
            phone_e164="+6283333333333",
            foundation_id=self.foundation.id,
            full_name="Siti Aminah (Guru Fisika)",
        )
        self.user_finance = User.all_tenants.create_user(
            phone_e164="+6284444444444",
            foundation_id=self.foundation.id,
            full_name="Dewi Sartika (Bendahara)",
        )

        # Assign roles
        assign_role(
            user=self.user_foundation_admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )
        assign_role(
            user=self.user_school_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
            foundation_id=self.foundation.id,
        )
        assign_role(
            user=self.user_teacher,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
            foundation_id=self.foundation.id,
        )
        assign_role(
            user=self.user_finance,
            role=ROLE_FINANCE_OFFICER,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_role_assignment_lifecycle(self):
        """Verify role assignment creation, unique constraint, and soft deletion."""
        assignment = RoleAssignment.all_tenants.get(
            user=self.user_school_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
        )
        self.assertEqual(assignment.scope_id, self.school_1.id)
        self.assertFalse(assignment.is_deleted)

        # Re-assigning idempotently does not duplicate
        re_assigned = assign_role(
            user=self.user_school_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
        )
        self.assertEqual(assignment.pk, re_assigned.pk)

        # Revoke role
        revoked = revoke_role(
            user=self.user_school_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_1.id,
        )
        self.assertTrue(revoked)
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_deleted)

        # Permissions should now be empty
        self.assertFalse(has_permission(
            self.user_school_admin,
            'school_config.read',
            self.foundation.id,
            self.school_1.id
        ))

    def test_baseline_permission_matrix(self):
        """Verify baseline matrix permissions per spec/02 §4.2."""
        # Foundation admin has broad RW
        self.assertTrue(has_permission(self.user_foundation_admin, 'school_config.write', self.foundation.id))
        self.assertTrue(has_permission(self.user_foundation_admin, 'payroll.write', self.foundation.id))
        self.assertTrue(has_permission(self.user_foundation_admin, 'finance.invoice.write', self.foundation.id))

        # Finance officer has invoices and payroll, but NOT school_config or grades
        self.assertTrue(has_permission(self.user_finance, 'finance.invoice.write', self.foundation.id))
        self.assertTrue(has_permission(self.user_finance, 'payroll.write', self.foundation.id))
        self.assertFalse(has_permission(self.user_finance, 'school_config.write', self.foundation.id))
        self.assertFalse(has_permission(self.user_finance, 'grades.write', self.foundation.id))

        # Teacher has grades and attendance, but NOT finance or payroll
        self.assertTrue(has_permission(self.user_teacher, 'grades.write', self.foundation.id, self.school_1.id))
        self.assertTrue(has_permission(self.user_teacher, 'attendance.write', self.foundation.id, self.school_1.id))
        self.assertFalse(has_permission(self.user_teacher, 'finance.invoice.read', self.foundation.id, self.school_1.id))
        self.assertFalse(has_permission(self.user_teacher, 'payroll.read', self.foundation.id, self.school_1.id))

    def test_school_scope_isolation_iam_012(self):
        """Verify school-scoped role cannot access sibling schools (IAM-012)."""
        # User is school_admin for school_1
        self.assertTrue(has_permission(
            self.user_school_admin,
            'school_config.read',
            self.foundation.id,
            self.school_1.id
        ))

        # Sibling school_2 access MUST return False
        self.assertFalse(has_permission(
            self.user_school_admin,
            'school_config.read',
            self.foundation.id,
            self.school_2.id
        ))

        # Foundation-wide context (no school_id) MUST return False for school-scoped roles
        self.assertFalse(has_permission(
            self.user_school_admin,
            'school_config.read',
            self.foundation.id,
            school_id=None
        ))

    def test_inactive_or_locked_user_denied(self):
        """Inactive or locked users must be denied all permissions."""
        self.user_teacher.is_active = False
        self.user_teacher.save()
        self.assertFalse(has_permission(
            self.user_teacher,
            'grades.write',
            self.foundation.id,
            self.school_1.id
        ))

    def test_drf_permission_fail_closed_iam_010(self):
        """DRF HasRequiredPermission fails closed if view has no required_permission (IAM-010)."""
        factory = APIRequestFactory()
        request = factory.get('/api/test/')
        request.user = self.user_foundation_admin

        perm = HasRequiredPermission()

        class ViewWithoutPermission:
            pass

        # Must raise PermissionDenied per IAM-010
        with self.assertRaises(PermissionDenied):
            perm.has_permission(request, ViewWithoutPermission())

    def test_drf_permission_evaluation(self):
        """DRF HasRequiredPermission evaluates permissions in tenant context."""
        factory = APIRequestFactory()
        perm = HasRequiredPermission()

        class SchoolConfigView:
            required_permission = 'school_config.read'
            kwargs = {'school_id': self.school_1.id}

        request = factory.get(f'/api/schools/{self.school_1.id}/config/')
        request.user = self.user_school_admin

        with tenant_context(self.foundation.id):
            self.assertTrue(perm.has_permission(request, SchoolConfigView()))

            # Now test access to sibling school 2
            SchoolConfigView.kwargs = {'school_id': self.school_2.id}
            self.assertFalse(perm.has_permission(request, SchoolConfigView()))

    def test_is_foundation_admin_permission_class(self):
        """IsFoundationAdmin permits only foundation_admin or superuser."""
        factory = APIRequestFactory()
        perm = IsFoundationAdmin()

        request = factory.get('/api/foundation/manage/')

        with tenant_context(self.foundation.id):
            request.user = self.user_foundation_admin
            self.assertTrue(perm.has_permission(request, None))

            request.user = self.user_school_admin
            self.assertFalse(perm.has_permission(request, None))

            request.user = self.user_teacher
            self.assertFalse(perm.has_permission(request, None))

    def test_clinic_officer_role_permissions(self):
        from apps.identity.rbac import ROLE_CLINIC_OFFICER, ROLE_PERMISSIONS
        perms = ROLE_PERMISSIONS[ROLE_CLINIC_OFFICER]
        self.assertIn('clinic.read', perms)
        self.assertIn('clinic.write', perms)
        self.assertIn('student_records.read', perms)

    def test_school_admin_and_foundation_admin_have_clinic_write(self):
        from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_PERMISSIONS
        self.assertIn('clinic.write', ROLE_PERMISSIONS[ROLE_FOUNDATION_ADMIN])
        self.assertIn('clinic.write', ROLE_PERMISSIONS[ROLE_SCHOOL_ADMIN])

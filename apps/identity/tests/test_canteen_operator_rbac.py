"""Tests for Canteen Operator Role and RBAC permissions (spec/07 §5, spec/02 §4)."""
from django.test import TestCase
from apps.identity.models import Foundation, Person, School, Student, User, RoleAssignment
from apps.identity.rbac import (
    assign_role,
    has_permission,
    get_user_permissions,
    ROLE_CANTEEN_OPERATOR,
    SCOPE_FOUNDATION,
    SCOPE_SCHOOL,
)
from apps.identity.guardian_access import is_staff_user, can_guardian_access_student
from educore.middleware.tenancy import clear_current_foundation_id


class CanteenOperatorRBACTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Lain",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Harapan Bangsa",
            npsn="20100001",
            level=School.LEVEL_SMP,
        )
        self.other_school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="20100002",
            level=School.LEVEL_SMA,
        )
        self.canteen_user = User.all_tenants.create_user(
            phone_e164="+6281234567890",
            foundation_id=self.foundation.id,
            full_name="Pak Joko (Operator Kantin)",
        )
        p1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101011111",
            full_name="Ahmad Dani",
        )
        self.student_smp = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=p1,
            nisn="1122334455",
            nis="SMP001",
        )
        p2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101012222",
            full_name="Budi Santoso",
        )
        self.student_sma = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.other_school,
            person=p2,
            nisn="1122334456",
            nis="SMA001",
        )

    def test_canteen_operator_role_assignment_and_permissions(self):
        assign_role(
            user=self.canteen_user,
            role=ROLE_CANTEEN_OPERATOR,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        perms = get_user_permissions(self.canteen_user, self.foundation.id, self.school.id)
        self.assertIn('student_records.read', perms)
        self.assertIn('wallet.topup.read', perms)
        self.assertIn('wallet.topup.write', perms)
        self.assertNotIn('grades.write', perms)
        self.assertNotIn('finance.invoice.write', perms)

        self.assertTrue(has_permission(self.canteen_user, 'student_records.read', self.foundation.id, self.school.id))
        self.assertFalse(has_permission(self.canteen_user, 'grades.write', self.foundation.id, self.school.id))

    def test_canteen_operator_staff_access(self):
        assign_role(
            user=self.canteen_user,
            role=ROLE_CANTEEN_OPERATOR,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Staff user in assigned school
        self.assertTrue(is_staff_user(self.canteen_user, self.foundation.id, school_id=self.school.id))
        self.assertTrue(is_staff_user(self.canteen_user, self.foundation.id))
        self.assertFalse(is_staff_user(self.canteen_user, self.other_foundation.id))

        # Can access student in their school
        self.assertTrue(can_guardian_access_student(self.canteen_user, self.student_smp.id, self.foundation.id))
        # Non-assigned school student access is False when scoped strictly to school
        self.assertFalse(is_staff_user(self.canteen_user, self.foundation.id, school_id=self.other_school.id))

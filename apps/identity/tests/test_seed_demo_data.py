"""Tests for the `seed_demo_data` management command."""
from io import StringIO

from django.core.management import CommandError, call_command
from django.db.models import Count
from django.test import TestCase

from apps.academic.models import ClassEnrollment, TimetableSlot
from apps.attendance.models import AttendanceDay
from apps.identity.models import (
    Foundation, GuardianLink, RoleAssignment, School, Staff, Student, User,
)
from educore.middleware.tenancy import clear_current_foundation_id


class SeedDemoDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        clear_current_foundation_id()
        cls.foundation = Foundation.objects.create(legal_name="Yayasan Demo", brand_name="Demo")
        cls.other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain")
        cls.school = School.all_tenants.create(
            foundation_id=cls.foundation.id, name="SD Demo", npsn="20199901", level=School.LEVEL_SD)
        cls.other_school = School.all_tenants.create(
            foundation_id=cls.other.id, name="SD Lain", npsn="20199902", level=School.LEVEL_SD)
        cls.admin = User.all_tenants.create_user(
            phone_e164="+6281200000099", email="admin@demo.test", foundation_id=cls.foundation.id, full_name="Admin")
        call_command("seed_demo_data", email="admin@demo.test", stdout=StringIO())

    def test_every_role_is_staffed_at_school_and_foundation_level(self):
        roles = set(RoleAssignment.all_tenants.filter(
            foundation_id=self.foundation.id, scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        ).values_list("role", flat=True))
        self.assertEqual(roles, {
            RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.ROLE_FINANCE_OFFICER, RoleAssignment.ROLE_TEACHER,
            RoleAssignment.ROLE_COUNSELLOR, RoleAssignment.ROLE_CANTEEN_OPERATOR, RoleAssignment.ROLE_CLINIC_OFFICER,
            RoleAssignment.ROLE_PARENT,
        })
        self.assertTrue(RoleAssignment.all_tenants.filter(
            foundation_id=self.foundation.id, role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION).exists())

    def test_at_least_300_active_students_each_enrolled_with_a_primary_guardian(self):
        active = Student.all_tenants.filter(school=self.school, status=Student.STATUS_ACTIVE)
        self.assertGreaterEqual(active.count(), 300)
        self.assertEqual(
            ClassEnrollment.all_tenants.filter(student__in=active, is_active=True).values("student").distinct().count(),
            active.count())
        primaries = GuardianLink.all_tenants.filter(student__in=active, is_primary=True) \
            .values("student").annotate(n=Count("id"))
        self.assertEqual(len(primaries), active.count())
        self.assertTrue(all(row["n"] == 1 for row in primaries))
        self.assertTrue(Student.all_tenants.filter(school=self.school, status=Student.STATUS_PROSPECT).exists())

    def test_staff_can_log_in_with_the_demo_password(self):
        staff_user = User.all_tenants.get(email="kepsek.sd@alhikmah.sch.id")
        self.assertTrue(staff_user.check_password("DemoStaff#2026"))
        self.assertTrue(Staff.all_tenants.filter(user=staff_user, school=self.school).exists())

    def test_teachers_are_never_double_booked(self):
        clashes = TimetableSlot.all_tenants.values(
            "class_subject__teacher_id", "day_of_week", "period_no").annotate(n=Count("id")).filter(n__gt=1)
        self.assertFalse(clashes.exists())

    def test_attendance_has_one_row_per_active_student_per_school_day(self):
        active = Student.all_tenants.filter(school=self.school, status=Student.STATUS_ACTIVE).count()
        self.assertEqual(AttendanceDay.all_tenants.filter(school=self.school).count(), active * 20)

    def test_rerun_is_idempotent_and_other_foundations_are_untouched(self):
        before = Student.all_tenants.count()
        call_command("seed_demo_data", email="admin@demo.test", stdout=StringIO())
        self.assertEqual(Student.all_tenants.count(), before)
        self.assertFalse(Student.all_tenants.filter(school=self.other_school).exists())

    def test_unknown_email_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", email="nobody@demo.test", stdout=StringIO())

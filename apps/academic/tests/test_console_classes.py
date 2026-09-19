"""Web console: Siswa & kelas pages (class list + class roster). Spec:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md (PR 1)."""
import datetime

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.models import Person, RoleAssignment, Student
from educore.middleware.tenancy import set_current_foundation_id

LIST_URL = '/web/academic/classes/'


class ClassConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Kelas Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.class_group = self.fx['class_group']
        self.teacher = self.fx['teacher']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.class_group, enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628133000002", "wali@kelas.test", "Wali Kelas",
            "3471010101022002", student=self.student,
        )
        self.client = APIClient()

    def _auth_teacher(self):
        self.client.force_authenticate(user=self.teacher.user)

    def _add_student(self, name, nis, is_active=True):
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person,
            nis=nis, nisn='9988776655', status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=student, class_group=self.class_group,
            enrolled_at=datetime.date(2026, 7, 1), is_active=is_active,
        )
        return student

    # --- class list -------------------------------------------------

    def test_list_renders_class_with_active_count_and_homeroom(self):
        self._add_student('Budi Santoso', 'X-002')
        self._add_student('Citra Lestari', 'X-003', is_active=False)
        self._auth_teacher()
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'X IPA 1')
        self.assertContains(res, 'Bu Siti Rahayu')
        self.assertContains(res, '2 / 36')  # Andi + Budi active; Citra inactive
        self.assertContains(res, f'/web/academic/classes/{self.class_group.id}/')

    def test_list_defaults_to_active_years_and_year_filter_overrides(self):
        old_year = AcademicYear.objects.create(
            foundation_id=self.foundation.id, school=self.school, name='2025/2026',
            start_date=datetime.date(2025, 7, 1), end_date=datetime.date(2026, 6, 30), is_active=False,
        )
        ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=self.school, academic_year=old_year,
            grade_level=9, name='IX Lama', homeroom_teacher=self.teacher,
        )
        self._auth_teacher()
        res = self.client.get(LIST_URL)
        self.assertContains(res, 'X IPA 1')
        self.assertNotContains(res, 'IX Lama')
        res = self.client.get(LIST_URL, {'academic_year': old_year.id})
        self.assertContains(res, 'IX Lama')
        self.assertNotContains(res, 'X IPA 1')
        # Non-numeric filter is ignored rather than erroring.
        res = self.client.get(LIST_URL, {'academic_year': 'abc'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'X IPA 1')

    def test_list_empty_state(self):
        ClassGroup.all_tenants.filter(id=self.class_group.id).update(deleted_at=timezone.now())
        self._auth_teacher()
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Belum ada kelas')

    def _query_count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        return len(ctx.captured_queries)

    def test_list_query_count_does_not_grow_with_class_count(self):
        self._auth_teacher()
        self.client.get(LIST_URL)  # warm per-process caches
        before = self._query_count(LIST_URL)
        for i in range(5):
            ClassGroup.objects.create(
                foundation_id=self.foundation.id, school=self.school,
                academic_year=self.fx['academic_year'], grade_level=10, name=f'X IPA {i + 2}',
                homeroom_teacher=self.teacher,
            )
        self.assertEqual(self._query_count(LIST_URL), before)

    # --- gating -----------------------------------------------------

    def test_pages_reject_guardian_without_staff_profile(self):
        # ROLE_PARENT holds student_records.read but has no Staff row -> 404.
        self.client.force_authenticate(user=self.guardian_user)
        self.assertEqual(self.client.get(LIST_URL).status_code, 404)
        self.assertEqual(self.client.get(f'{LIST_URL}{self.class_group.id}/').status_code, 404)

    def test_pages_require_authentication(self):
        self.assertIn(self.client.get(LIST_URL).status_code, (401, 403))
        self.assertIn(self.client.get(f'{LIST_URL}{self.class_group.id}/').status_code, (401, 403))

    # --- class detail -----------------------------------------------

    def test_detail_shows_roster_name_and_nis_only(self):
        self._add_student('Budi Santoso', 'X-002')
        self._add_student('Citra Lestari', 'X-003', is_active=False)
        self._auth_teacher()
        res = self.client.get(f'{LIST_URL}{self.class_group.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Andi Wijaya')
        self.assertContains(res, 'X-001')
        self.assertContains(res, 'Budi Santoso')
        self.assertNotContains(res, 'Citra Lestari')  # inactive enrolment
        # PII never rendered: NISN and NIK
        self.assertNotContains(res, '1122334455')
        self.assertNotContains(res, '9988776655')
        self.assertNotContains(res, '3471010101010202')

    def test_detail_empty_roster_state(self):
        self.class_group.enrollments.update(is_active=False)
        self._auth_teacher()
        res = self.client.get(f'{LIST_URL}{self.class_group.id}/')
        self.assertContains(res, 'Belum ada siswa')

    def test_detail_unknown_class_returns_404(self):
        self._auth_teacher()
        self.assertEqual(self.client.get(f'{LIST_URL}999999/').status_code, 404)

    def test_detail_cross_tenant_class_returns_404(self):
        other = build_academic_fixture("Yayasan Lain Kelas")
        set_current_foundation_id(self.foundation.id)
        self._auth_teacher()
        res = self.client.get(f'{LIST_URL}{other["class_group"].id}/')
        self.assertEqual(res.status_code, 404)

    def test_list_does_not_leak_other_foundation_classes(self):
        other = build_academic_fixture("Yayasan Lain Kelas 2")
        ClassGroup.all_tenants.filter(id=other['class_group'].id).update(name='RAHASIA LAIN')
        set_current_foundation_id(self.foundation.id)
        self._auth_teacher()
        res = self.client.get(LIST_URL)
        self.assertNotContains(res, 'RAHASIA LAIN')

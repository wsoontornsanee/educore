import datetime
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Person, RoleAssignment, Student
from apps.reporting.models import RptActiveStudent
from apps.reporting.services import refresh_active_students


class RefreshActiveStudentsTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.current_month = timezone.now().date().replace(day=1)

    def _enroll(self, student):
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=student,
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_active_enrolled_student_counted(self):
        self._enroll(self.fx['student'])
        refresh_active_students(scope='dashboard')

        row = RptActiveStudent.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=self.current_month,
        )
        self.assertEqual(row.active_count, 1)

    def test_inactive_student_excluded(self):
        self._enroll(self.fx['student'])
        self.fx['student'].status = Student.STATUS_INACTIVE
        self.fx['student'].save()
        refresh_active_students(scope='dashboard')

        row = RptActiveStudent.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=self.current_month,
        )
        self.assertEqual(row.active_count, 0)

    def test_unenrolled_student_excluded(self):
        # fx['student'] is ACTIVE but never given a ClassEnrollment in this test.
        refresh_active_students(scope='dashboard')

        row = RptActiveStudent.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=self.current_month,
        )
        self.assertEqual(row.active_count, 0)

    def test_closed_past_month_is_immutable(self):
        past_month = datetime.date(2020, 1, 1)
        RptActiveStudent.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=past_month,
            active_count=1, computed_at=timezone.now(),
        )
        self._enroll(self.fx['student'])  # would make the "true" count 1 if recomputed with more students below
        person2 = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3471010101017777', full_name='Siswa Baru')
        student2 = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], person=person2,
            nisn='5544332211', nis='X-003', status=Student.STATUS_ACTIVE,
        )
        self._enroll(student2)

        refresh_active_students(since=past_month, scope='full')

        row = RptActiveStudent.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=past_month)
        self.assertEqual(row.active_count, 1)  # untouched, NOT recomputed to 2 (RPT-008)

    def test_full_scope_backfills_missing_previous_month_once(self):
        self._enroll(self.fx['student'])
        refresh_active_students(scope='full')

        previous_month = (self.current_month - datetime.timedelta(days=1)).replace(day=1)
        self.assertTrue(RptActiveStudent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=previous_month,
        ).exists())
        self.assertTrue(RptActiveStudent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=self.current_month,
        ).exists())

    def test_open_current_month_recomputes_on_rerun(self):
        refresh_active_students(scope='dashboard')
        row = RptActiveStudent.all_tenants.get(foundation_id=self.fx['foundation'].id, school=self.fx['school'], month=self.current_month)
        self.assertEqual(row.active_count, 0)

        self._enroll(self.fx['student'])
        refresh_active_students(scope='dashboard')
        row.refresh_from_db()
        self.assertEqual(row.active_count, 1)


class ActiveStudentsViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        refresh_active_students(scope='dashboard')

    def test_active_students_report_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/reporting/active-students/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['active_count'], 1)

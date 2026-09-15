import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import AttendanceDay, AttendanceSource, AttendanceStatus
from apps.identity.models import Person, RoleAssignment, Student, User
from apps.reporting.models import RptDailyAttendance
from apps.reporting.services import refresh_daily_attendance


def make_attendance_day(fx, student, date, status):
    return AttendanceDay.objects.create(
        foundation_id=fx['foundation'].id, school=fx['school'], student=student,
        date=date, status=status, source=AttendanceSource.GATE,
    )


class RefreshDailyAttendanceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        self.today = timezone.now().date()

    def test_computes_counts_and_rate_pct(self):
        make_attendance_day(self.fx, self.fx['student'], self.today, AttendanceStatus.HADIR)
        refresh_daily_attendance(scope='full')

        row = RptDailyAttendance.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            date=self.today, class_group=self.fx['class_group'],
        )
        self.assertEqual(row.present, 1)
        self.assertEqual(row.late, 0)
        self.assertEqual(row.rate_pct, Decimal('100.00'))

    def test_dispen_bucketed_into_permitted(self):
        make_attendance_day(self.fx, self.fx['student'], self.today, AttendanceStatus.DISPEN)
        refresh_daily_attendance(scope='full')

        row = RptDailyAttendance.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            date=self.today, class_group=self.fx['class_group'],
        )
        self.assertEqual(row.permitted, 1)

    def test_late_counts_toward_rate_pct(self):
        make_attendance_day(self.fx, self.fx['student'], self.today, AttendanceStatus.TERLAMBAT)
        refresh_daily_attendance(scope='full')

        row = RptDailyAttendance.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            date=self.today, class_group=self.fx['class_group'],
        )
        self.assertEqual(row.late, 1)
        self.assertEqual(row.rate_pct, Decimal('100.00'))

    def test_student_without_active_enrollment_skipped(self):
        person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3471010101019999', full_name='Tanpa Kelas')
        unenrolled_student = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], person=person,
            nisn='9988776655', nis='X-999', status=Student.STATUS_ACTIVE,
        )
        make_attendance_day(self.fx, unenrolled_student, self.today, AttendanceStatus.HADIR)
        refresh_daily_attendance(scope='full')

        self.assertFalse(RptDailyAttendance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, date=self.today,
        ).exists())

    def test_dashboard_scope_excludes_old_days_full_scope_includes_them(self):
        old_date = self.today - datetime.timedelta(days=10)
        make_attendance_day(self.fx, self.fx['student'], old_date, AttendanceStatus.HADIR)

        refresh_daily_attendance(scope='dashboard')
        self.assertFalse(RptDailyAttendance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, date=old_date,
        ).exists())

        refresh_daily_attendance(scope='full')
        self.assertTrue(RptDailyAttendance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, date=old_date,
        ).exists())

    def test_rerun_is_idempotent(self):
        make_attendance_day(self.fx, self.fx['student'], self.today, AttendanceStatus.HADIR)
        refresh_daily_attendance(scope='full')
        refresh_daily_attendance(scope='full')

        self.assertEqual(
            RptDailyAttendance.all_tenants.filter(
                foundation_id=self.fx['foundation'].id, date=self.today, class_group=self.fx['class_group'],
            ).count(),
            1,
        )


class DailyAttendanceViewTests(TestCase):
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
        today = timezone.now().date()
        make_attendance_day(self.fx, self.fx['student'], today, AttendanceStatus.HADIR)
        refresh_daily_attendance(scope='full')

    def test_daily_attendance_report_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/reporting/daily-attendance/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['present'], 1)

    def test_filter_by_class_group_id(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(
            f'/api/v1/reporting/daily-attendance/?school_id={self.fx["school"].id}&class_group_id={self.fx["class_group"].id + 1}'
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['rows']), 0)

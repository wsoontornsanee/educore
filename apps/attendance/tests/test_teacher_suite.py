import datetime
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.academic.models import (
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    DayOfWeek,
)
from apps.academic.services import create_timetable_slot, assign_substitution
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import AttendanceDay, AttendanceSource, AttendanceStatus, PeriodAttendance
from apps.attendance.services import get_teacher_agenda, submit_period_attendance
from educore.middleware.tenancy import set_current_foundation_id


def make_second_teacher(fx):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik='3471010101010505', full_name='Pak Joko')
    user = User.objects.create(foundation_id=fx['foundation'].id, phone_e164='+628144444444', email='joko@cendekia.sch.id', full_name='Pak Joko')
    return Staff.all_tenants.create(
        foundation_id=fx['foundation'].id, person=person, user=user, school=fx['school'],
        join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
    )


class TeacherAgendaTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.monday = datetime.date(2026, 8, 3)  # a Monday
        assert self.monday.isoweekday() == DayOfWeek.MONDAY
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )

    def test_agenda_includes_own_slot(self):
        agenda = get_teacher_agenda(self.fx['teacher'], self.monday)
        self.assertEqual(len(agenda), 1)
        self.assertFalse(agenda[0]['is_substitution'])

    def test_agenda_excludes_other_teacher(self):
        other = make_second_teacher(self.fx)
        agenda = get_teacher_agenda(other, self.monday)
        self.assertEqual(agenda, [])

    def test_agenda_includes_substitution(self):
        other = make_second_teacher(self.fx)
        assign_substitution(self.slot, self.monday, other, reason="Sakit")
        agenda = get_teacher_agenda(other, self.monday)
        self.assertEqual(len(agenda), 1)
        self.assertTrue(agenda[0]['is_substitution'])

    def test_agenda_wrong_weekday_empty(self):
        tuesday = self.monday + datetime.timedelta(days=1)
        agenda = get_teacher_agenda(self.fx['teacher'], tuesday)
        self.assertEqual(agenda, [])


class PeriodAttendanceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.date = datetime.date(2026, 8, 3)
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_defaults_to_hadir(self):
        records = submit_period_attendance(self.fx['teacher'], self.slot, self.date, {})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, AttendanceStatus.HADIR)

    def test_gate_prefill_alpa(self):
        AttendanceDay.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            date=self.date,
            status=AttendanceStatus.ALPA,
            source=AttendanceSource.GATE,
        )
        records = submit_period_attendance(self.fx['teacher'], self.slot, self.date, {})
        self.assertEqual(records[0].status, AttendanceStatus.ALPA)
        self.assertEqual(records[0].source, 'GATE_PREFILL')

    def test_teacher_exception_overrides_gate_prefill(self):
        AttendanceDay.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            date=self.date,
            status=AttendanceStatus.ALPA,
            source=AttendanceSource.GATE,
        )
        records = submit_period_attendance(
            self.fx['teacher'], self.slot, self.date, {self.fx['student'].id: AttendanceStatus.IZIN}
        )
        self.assertEqual(records[0].status, AttendanceStatus.IZIN)
        self.assertEqual(records[0].source, 'TEACHER')

    def test_resubmit_is_idempotent(self):
        submit_period_attendance(self.fx['teacher'], self.slot, self.date, {})
        submit_period_attendance(
            self.fx['teacher'], self.slot, self.date, {self.fx['student'].id: AttendanceStatus.SAKIT}
        )
        self.assertEqual(PeriodAttendance.objects.filter(slot=self.slot, date=self.date).count(), 1)
        record = PeriodAttendance.objects.get(slot=self.slot, date=self.date)
        self.assertEqual(record.status, AttendanceStatus.SAKIT)


class TeacherSuiteViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        self.date = datetime.date(2026, 8, 3)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        self.slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_agenda_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/teacher/agenda/?date={self.date.isoformat()}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['agenda']), 1)

    def test_submit_period_attendance_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(
            f'/api/v1/timetable/slots/{self.slot.id}/period-attendance/',
            {'date': self.date.isoformat(), 'exceptions': [{'student_id': self.fx['student'].id, 'status': 'IZIN'}]},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['statuses'][str(self.fx['student'].id)], 'IZIN')

    def test_teacher_forbidden_from_finance_invoices(self):
        """TCH-012: teacher must never see financial data."""
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get('/api/v1/finance/invoices/')
        self.assertEqual(res.status_code, 403)

    def test_cross_tenant_period_attendance_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan B")
        RoleAssignment.all_tenants.create(
            foundation_id=fx_b['foundation'].id,
            user=fx_b['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=fx_b['school'].id,
        )
        self.client.force_authenticate(user=fx_b['teacher_user'])
        res = self.client.post(
            f'/api/v1/timetable/slots/{self.slot.id}/period-attendance/',
            {'date': self.date.isoformat(), 'exceptions': []},
            format='json',
        )
        self.assertEqual(res.status_code, 404)

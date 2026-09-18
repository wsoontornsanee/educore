"""approve_absence_request must cascade its day-level SAKIT/IZIN override to
PeriodAttendance for every scheduled period on each covered date — the same gap
LIF-003 fixed for clinic visits, generalized via
apps.attendance.services.mark_period_attendance_for_day."""
import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academic.models import DayOfWeek, TimetableSlot
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import (
    AbsenceRequest,
    AbsenceRequestStatus,
    AbsenceType,
    AttendanceStatus,
    PeriodAttendance,
    PeriodAttendanceSource,
)
from apps.attendance.services import approve_absence_request
from apps.core.models import AuditEvent


def add_slot(fx, period_no, start_time, end_time, weekday):
    return TimetableSlot.objects.create(
        foundation_id=fx['foundation'].id,
        class_subject=fx['class_subject'],
        day_of_week=weekday,
        period_no=period_no,
        start_time=start_time,
        end_time=end_time,
    )


class ApproveAbsenceRequestPeriodCascadeTests(TestCase):
    def setUp(self):
        from apps.academic.models import ClassEnrollment

        self.fx = build_academic_fixture()
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
            is_active=True,
        )

        self.monday = datetime.date(2026, 9, 21)
        self.tuesday = datetime.date(2026, 9, 22)
        self.assertEqual(self.monday.isoweekday(), DayOfWeek.MONDAY)
        self.assertEqual(self.tuesday.isoweekday(), DayOfWeek.TUESDAY)

        self.mon_slot = add_slot(self.fx, 1, datetime.time(7, 0), datetime.time(7, 40), DayOfWeek.MONDAY)
        self.tue_slot = add_slot(self.fx, 1, datetime.time(7, 0), datetime.time(7, 40), DayOfWeek.TUESDAY)

        # Monday's period was already taught (e.g. the student attended before the
        # absence-request window started, or a teacher retroactively recorded it).
        PeriodAttendance.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            slot=self.mon_slot,
            date=self.monday,
            status=AttendanceStatus.HADIR,
            source=PeriodAttendanceSource.TEACHER,
        )

        self.request = AbsenceRequest.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            requested_by=self.fx['teacher_user'],
            date_from=self.monday,
            date_to=self.tuesday,
            type=AbsenceType.SAKIT,
            reason='Sakit demam',
            status=AbsenceRequestStatus.PENDING,
        )

    def test_approval_marks_every_covered_date_sakit(self):
        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])

        tuesday_period = PeriodAttendance.objects.get(
            student=self.fx['student'], slot=self.tue_slot, date=self.tuesday,
        )
        self.assertEqual(tuesday_period.status, AttendanceStatus.SAKIT)
        self.assertEqual(tuesday_period.source, PeriodAttendanceSource.MANUAL)

    def test_does_not_overwrite_already_recorded_period(self):
        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])

        monday_period = PeriodAttendance.objects.get(
            student=self.fx['student'], slot=self.mon_slot, date=self.monday,
        )
        self.assertEqual(monday_period.status, AttendanceStatus.HADIR)
        self.assertEqual(monday_period.source, PeriodAttendanceSource.TEACHER)

    def test_izin_type_cascades_as_izin(self):
        self.request.type = AbsenceType.IZIN
        self.request.save(update_fields=['type'])
        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])

        tuesday_period = PeriodAttendance.objects.get(
            student=self.fx['student'], slot=self.tue_slot, date=self.tuesday,
        )
        self.assertEqual(tuesday_period.status, AttendanceStatus.IZIN)

    def test_writes_audit_event_for_periods_overridden(self):
        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])

        event = AuditEvent.objects.get(
            action='attendance.absence_request.periods_overridden',
            entity_type='AbsenceRequest',
            entity_id=self.request.id,
        )
        self.assertEqual(event.diff['slot_ids_by_date'][self.tuesday.isoformat()], [self.tue_slot.id])
        self.assertNotIn(self.monday.isoformat(), event.diff['slot_ids_by_date'])

    def test_no_audit_event_when_no_new_periods_created(self):
        # Pre-fill both dates so the cascade has nothing left to create.
        PeriodAttendance.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'], slot=self.tue_slot,
            date=self.tuesday, status=AttendanceStatus.HADIR, source=PeriodAttendanceSource.TEACHER,
        )
        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])
        self.assertFalse(
            AuditEvent.objects.filter(
                action='attendance.absence_request.periods_overridden', entity_id=self.request.id,
            ).exists()
        )

    def test_idempotent_on_rerun_does_not_duplicate(self):
        from apps.attendance.services import mark_period_attendance_for_day

        approve_absence_request(self.request, decided_by=self.fx['teacher_user'])
        mark_period_attendance_for_day(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            date=self.tuesday, status=AttendanceStatus.SAKIT, source=PeriodAttendanceSource.MANUAL,
            note='re-run', user=self.fx['teacher_user'],
        )
        self.assertEqual(
            PeriodAttendance.objects.filter(student=self.fx['student'], slot=self.tue_slot, date=self.tuesday).count(),
            1,
        )

import datetime
import io
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.academic.models import (
    AcademicCalendarEvent,
    AcademicCalendarEventType,
    AcademicYear,
    ClassEnrollment,
    ClassGroup,
    Term,
)
from apps.attendance.models import (
    AbsenceRequest,
    AbsenceRequestStatus,
    AbsenceType,
    AttendanceDay,
    AttendanceRule,
    AttendanceSource,
    AttendanceStatus,
    GateDirection,
    GateEvent,
    GateEventStatus,
    GateMethod,
)
from apps.attendance.services import mark_absent_students_for_school
from apps.core.models import AuditEvent, DomainEvent, JobRun
from apps.hardware.models import Device, DeviceClass, DeviceDirection
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.notifications.models import NotificationCategory, NotificationIntent
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class MarkAbsentStudentsTests(TestCase):
    """
    Comprehensive test suite for automated daily absence sweep (spec/05 §3 ATT-001..005, deploy/crontab:19).
    """

    def setUp(self):
        # Foundation 1
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Harapan Bangsa",
            brand_name="Harapan Bangsa",
            status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)

        # School 1
        self.school = School.objects.create(
            foundation_id=self.foundation.id,
            name="SMA Harapan Bangsa",
            npsn="99988877",
            level=School.LEVEL_SMA,
            timezone="Asia/Jakarta",
        )

        self.device = Device.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            device_code="GATE-001",
            name="Main Gate 1",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.IN,
            ip_address="192.168.1.50",
            mac_address="00:11:22:33:44:55",
        )

        # Attendance Rule with cutoff at 09:00 WIB
        self.rule = AttendanceRule.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            absent_cutoff_time=datetime.time(9, 0, 0),
            late_after_time=datetime.time(7, 15, 0),
        )

        # Student 1 (Active)
        self.person1 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3201010101010001",
            full_name="Budi Pratama",
        )
        self.student1 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person1,
            nis="NIS001",
            status=Student.STATUS_ACTIVE,
        )

        # Student 2 (Active)
        self.person2 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3201010101010002",
            full_name="Siti Rahma",
        )
        self.student2 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person2,
            nis="NIS002",
            status=Student.STATUS_ACTIVE,
        )

        # Student 3 (Inactive / Transferred out - should NOT be evaluated)
        self.person3 = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3201010101010003",
            full_name="Rian Santoso",
        )
        self.student3 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.person3,
            nis="NIS003",
            status=Student.STATUS_INACTIVE,
        )

        # Guardian for Student 1
        self.guardian_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3201010101010004",
            full_name="Pak Joko Pratama",
        )
        self.guardian_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="joko@example.com",
            full_name="Pak Joko Pratama",
        )
        self.guardian = Guardian.objects.create(
            foundation_id=self.foundation.id,
            person=self.guardian_person,
            user=self.guardian_user,
        )
        GuardianLink.objects.create(
            foundation_id=self.foundation.id,
            student=self.student1,
            guardian=self.guardian,
            relation="FATHER",
            is_primary=True,
        )

        # Reference Monday date (2026-09-14 is a Monday)
        self.weekday_date = datetime.date(2026, 9, 14)

    def test_mark_absent_students_marks_unrecorded_as_alpa(self):
        """Active students with no scan or attendance record are marked ALPA at cutoff (ATT-001)."""
        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'COMPLETED')
        self.assertEqual(res['total_students'], 2)  # student1 and student2 (student3 inactive)
        self.assertEqual(res['marked_alpa'], 2)

        # Verify AttendanceDay records created
        att1 = AttendanceDay.objects.get(student=self.student1, date=self.weekday_date)
        self.assertEqual(att1.status, AttendanceStatus.ALPA)
        self.assertEqual(att1.source, AttendanceSource.SYSTEM)
        self.assertFalse(att1.is_override)

        att2 = AttendanceDay.objects.get(student=self.student2, date=self.weekday_date)
        self.assertEqual(att2.status, AttendanceStatus.ALPA)
        self.assertEqual(att2.source, AttendanceSource.SYSTEM)

        # Verify audit and domain events
        self.assertTrue(
            AuditEvent.objects.filter(
                action='attendance.day.auto_marked_alpa',
                entity_id=att1.id,
            ).exists()
        )
        self.assertTrue(
            DomainEvent.objects.filter(
                name='attendance.day.auto_marked_alpa',
                payload__student_id=str(self.student1.id),
            ).exists()
        )

    def test_mark_absent_skips_students_with_gate_scan(self):
        """Students with an accepted gate check-in are not marked ALPA."""
        import uuid

        # Create gate scan for student1
        scan_time = datetime.datetime.combine(self.weekday_date, datetime.time(7, 10, 0), tzinfo=datetime.timezone.utc)
        GateEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            device=self.device,
            student=self.student1,
            event_uuid=uuid.uuid4(),
            direction=GateDirection.IN,
            occurred_at=scan_time,
            method=GateMethod.RFID,
            status=GateEventStatus.ACCEPTED,
        )

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'COMPLETED')
        self.assertEqual(res['already_recorded'], 1)  # student1 skipped
        self.assertEqual(res['marked_alpa'], 1)       # student2 marked ALPA

        self.assertFalse(AttendanceDay.objects.filter(student=self.student1, date=self.weekday_date).exists())
        self.assertTrue(AttendanceDay.objects.filter(student=self.student2, date=self.weekday_date, status=AttendanceStatus.ALPA).exists())

    def test_mark_absent_applies_approved_absence_request(self):
        """Approved absence request marks SAKIT / IZIN instead of ALPA (ATT-002)."""
        AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student1,
            requested_by=self.guardian_user,
            type=AbsenceType.SAKIT,
            status=AbsenceRequestStatus.APPROVED,
            date_from=self.weekday_date,
            date_to=self.weekday_date,
            reason="Demam tinggi",
        )

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['excused_from_requests'], 1)
        self.assertEqual(res['marked_alpa'], 1)  # student2

        # student1 has SAKIT
        att1 = AttendanceDay.objects.get(student=self.student1, date=self.weekday_date)
        self.assertEqual(att1.status, AttendanceStatus.SAKIT)
        self.assertEqual(att1.source, AttendanceSource.MANUAL)
        self.assertTrue(att1.is_override)
        self.assertIn("Demam tinggi", att1.note)

    def test_mark_absent_preserves_existing_attendance_and_overrides(self):
        """Pre-existing attendance entries and staff overrides are preserved (ATT-003)."""
        # Teacher pre-marked student1 as HADIR
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student1,
            date=self.weekday_date,
            status=AttendanceStatus.HADIR,
            source=AttendanceSource.TEACHER,
        )
        # Staff manual override for student2 to DISPEN
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student2,
            date=self.weekday_date,
            status=AttendanceStatus.DISPEN,
            source=AttendanceSource.MANUAL,
            is_override=True,
            note="Lomba olimpiade sains",
        )

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['already_recorded'], 2)
        self.assertEqual(res['marked_alpa'], 0)

        # Records are unchanged
        self.assertEqual(AttendanceDay.objects.get(student=self.student1, date=self.weekday_date).status, AttendanceStatus.HADIR)
        self.assertEqual(AttendanceDay.objects.get(student=self.student2, date=self.weekday_date).status, AttendanceStatus.DISPEN)

    def test_mark_absent_skips_sunday(self):
        """Sundays are non-school days and must not generate ALPA (ATT-005)."""
        sunday_date = datetime.date(2026, 9, 13)  # Sunday (2026-09-13)
        self.assertEqual(sunday_date.weekday(), 6)

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=sunday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'SKIPPED_NON_SCHOOL_DAY')
        self.assertEqual(res['marked_alpa'], 0)
        self.assertEqual(AttendanceDay.objects.filter(date=sunday_date).count(), 0)

    def test_mark_absent_skips_school_wide_holiday(self):
        """School-wide academic calendar holiday must not generate ALPA (ATT-005)."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo('Asia/Jakarta')
        holiday_start = datetime.datetime.combine(self.weekday_date, datetime.time(0, 0), tzinfo=tz)
        holiday_end = datetime.datetime.combine(self.weekday_date, datetime.time(23, 59, 59), tzinfo=tz)

        AcademicCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            event_type=AcademicCalendarEventType.HOLIDAY,
            title="Hari Libur Nasional Maulid Nabi",
            start_at=holiday_start,
            end_at=holiday_end,
            affects_attendance=True,
        )

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'SKIPPED_HOLIDAY')
        self.assertEqual(res['marked_alpa'], 0)
        self.assertEqual(AttendanceDay.objects.filter(date=self.weekday_date).count(), 0)

    def test_mark_absent_skips_class_specific_holiday(self):
        """Class-specific holiday exempts only students in that class group (ATT-005)."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo('Asia/Jakarta')
        holiday_start = datetime.datetime.combine(self.weekday_date, datetime.time(0, 0), tzinfo=tz)
        holiday_end = datetime.datetime.combine(self.weekday_date, datetime.time(23, 59, 59), tzinfo=tz)

        ay = AcademicYear.objects.create(foundation_id=self.foundation.id, school=self.school, name="2026/2027", start_date="2026-07-01", end_date="2027-06-30")
        term = Term.objects.create(foundation_id=self.foundation.id, academic_year=ay, term_no=1, name="Ganjil", start_date="2026-07-01", end_date="2026-12-31")
        cg1 = ClassGroup.objects.create(foundation_id=self.foundation.id, school=self.school, academic_year=ay, name="X-A", grade_level=10)
        cg2 = ClassGroup.objects.create(foundation_id=self.foundation.id, school=self.school, academic_year=ay, name="X-B", grade_level=10)

        # Student1 in cg1, Student2 in cg2
        ClassEnrollment.objects.create(foundation_id=self.foundation.id, student=self.student1, class_group=cg1, enrolled_at=datetime.date(2026, 7, 1), is_active=True)
        ClassEnrollment.objects.create(foundation_id=self.foundation.id, student=self.student2, class_group=cg2, enrolled_at=datetime.date(2026, 7, 1), is_active=True)

        holiday = AcademicCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            event_type=AcademicCalendarEventType.HOLIDAY,
            title="Studi Lapangan Khusus Kelas X-A",
            start_at=holiday_start,
            end_at=holiday_end,
            affects_attendance=True,
        )
        holiday.class_groups.add(cg1)

        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'COMPLETED')
        self.assertEqual(res['holiday_exempt'], 1)  # student1 exempt
        self.assertEqual(res['marked_alpa'], 1)     # student2 marked ALPA

        self.assertFalse(AttendanceDay.objects.filter(student=self.student1, date=self.weekday_date).exists())
        self.assertTrue(AttendanceDay.objects.filter(student=self.student2, date=self.weekday_date, status=AttendanceStatus.ALPA).exists())

    def test_mark_absent_before_cutoff_time_skipped(self):
        """If target_date is today and current time is before absent_cutoff_time, sweep skips."""
        # Mock local time to 08:30 WIB (before cutoff at 09:00 WIB)
        early_time = datetime.datetime(2026, 9, 14, 8, 30, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=7)))
        with mock.patch('django.utils.timezone.now', return_value=early_time):
            res = mark_absent_students_for_school(
                school=self.school,
                target_date=datetime.date(2026, 9, 14),
                force_cutoff=False,
            )
            self.assertEqual(res['status'], 'SKIPPED_BEFORE_CUTOFF')
            self.assertEqual(res['marked_alpa'], 0)

            # With force_cutoff=True, runs anyway
            res_forced = mark_absent_students_for_school(
                school=self.school,
                target_date=datetime.date(2026, 9, 14),
                force_cutoff=True,
            )
            self.assertEqual(res_forced['status'], 'COMPLETED')
            self.assertEqual(res_forced['marked_alpa'], 2)

    def test_mark_absent_dispatches_guardian_notification(self):
        """Guardian receives an absence notification intent when student is marked ALPA."""
        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )

        self.assertGreater(res['notifications_dispatched'], 0)

        # Verify NotificationIntent was created for student1's guardian
        intent = NotificationIntent.objects.filter(
            recipient_user=self.guardian_user,
            category=NotificationCategory.ABSENCE,
        ).first()

        self.assertIsNotNone(intent)
        self.assertEqual(intent.template_key, 'attendance.absent')
        self.assertEqual(intent.payload['student_id'], self.student1.id)
        self.assertEqual(intent.payload['student_name'], 'Budi Pratama')
        self.assertEqual(intent.payload['school_name'], 'SMA Harapan Bangsa')
        self.assertEqual(intent.payload['cutoff_time'], '09:00')

    def test_mark_absent_idempotent_rerun(self):
        """Re-running the sweep multiple times is completely idempotent."""
        res1 = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )
        self.assertEqual(res1['marked_alpa'], 2)
        initial_intents_count = NotificationIntent.objects.count()

        # Run second time
        res2 = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            force_cutoff=True,
        )
        self.assertEqual(res2['marked_alpa'], 0)
        self.assertEqual(res2['already_recorded'], 2)
        self.assertEqual(res2['notifications_dispatched'], 0)

        # No duplicate attendance rows or notifications created
        self.assertEqual(AttendanceDay.objects.filter(date=self.weekday_date).count(), 2)
        self.assertEqual(NotificationIntent.objects.count(), initial_intents_count)

    def test_mark_absent_dry_run(self):
        """Dry-run previews counts without persisting AttendanceDay or NotificationIntent."""
        res = mark_absent_students_for_school(
            school=self.school,
            target_date=self.weekday_date,
            dry_run=True,
            force_cutoff=True,
        )

        self.assertEqual(res['status'], 'DRY_RUN')
        self.assertEqual(res['marked_alpa'], 2)
        self.assertEqual(res['notifications_dispatched'], 0)

        # Nothing written to database
        self.assertEqual(AttendanceDay.objects.filter(date=self.weekday_date).count(), 0)
        self.assertEqual(NotificationIntent.objects.count(), 0)

    def test_mark_absent_multi_tenant_isolation(self):
        """Students from other foundations are never evaluated or affected."""
        f2 = Foundation.objects.create(
            legal_name="Yayasan Bintang Gemilang",
            brand_name="Bintang Gemilang",
            status=Foundation.STATUS_ACTIVE,
        )
        s2 = School.objects.create(
            foundation_id=f2.id,
            name="SMA Bintang Gemilang",
            npsn="11122233",
            level=School.LEVEL_SMA,
        )
        p_f2 = Person.all_tenants.create(
            foundation_id=f2.id,
            nik="3301010101010001",
            full_name="Dewi Lestari",
        )
        st_f2 = Student.all_tenants.create(
            foundation_id=f2.id,
            school=s2,
            person=p_f2,
            nis="NIS_F2_001",
            status=Student.STATUS_ACTIVE,
        )

        # Run sweep for Foundation 1 only
        with tenant_context(self.foundation.id):
            res = mark_absent_students_for_school(
                school=self.school,
                target_date=self.weekday_date,
                force_cutoff=True,
            )
            self.assertEqual(res['total_students'], 2)

        # Foundation 2 student has no attendance record
        self.assertFalse(AttendanceDay.all_tenants.filter(foundation_id=f2.id).exists())

    def test_management_command_execution_and_job_run(self):
        """Management command executes cleanly and writes JobRun entry (ARC-008, ARC-013)."""
        out = io.StringIO()
        call_command(
            'mark_absent_students',
            f'--date={self.weekday_date.isoformat()}',
            f'--school-id={self.school.id}',
            '--force-cutoff',
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("Starting automated absence sweep", output)
        self.assertIn("Absence sweep completed", output)

        # Verify JobRun entry
        job_run = JobRun.objects.filter(job_name='mark_absent_students').latest('id')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 2)
        self.assertIsNotNone(job_run.finished_at)

        # Verify records created
        self.assertEqual(AttendanceDay.objects.filter(date=self.weekday_date).count(), 2)

    def test_management_command_advisory_lock_contention(self):
        """Management command exits cleanly when advisory lock is already held (ARC-007)."""
        from apps.core.locks import advisory_lock

        with advisory_lock('mark_absent_students', timeout=0):
            out = io.StringIO()
            # Try to run while lock is held
            call_command('mark_absent_students', stdout=out)
            output = out.getvalue()
            self.assertIn("already held. Exiting immediately.", output)

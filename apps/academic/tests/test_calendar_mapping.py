import datetime
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.calendar_services import (
    classify_calendar_event,
    map_external_event_to_academic,
    sync_external_calendar_events_to_academic,
)
from apps.academic.models import (
    AcademicCalendarEvent,
    AcademicCalendarEventType,
    CalendarAcademicSyncPolicy,
    Exam,
    TimetableSlot,
)
from apps.academic.services import get_expected_periods_for_school
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.services import get_teacher_agenda
from apps.calendar_sync.crypto import encrypt_secret
from apps.calendar_sync.models import CalendarConnection, ExternalCalendarEvent
from apps.identity.models import Foundation, RoleAssignment
from educore.middleware.tenancy import set_current_foundation_id


class CalendarClassificationTests(TestCase):
    def test_explicit_bracket_tags(self):
        etype, affects = classify_calendar_event("[UJIAN] Penilaian Tengah Semester")
        self.assertEqual(etype, AcademicCalendarEventType.EXAM)
        self.assertTrue(affects)

        etype, affects = classify_calendar_event("[LIBUR] Hari Kemerdekaan RI")
        self.assertEqual(etype, AcademicCalendarEventType.HOLIDAY)
        self.assertTrue(affects)

        etype, affects = classify_calendar_event("[BATAL] Jam Pelajaran Ke-3")
        self.assertEqual(etype, AcademicCalendarEventType.TIMETABLE_EXCEPTION)
        self.assertTrue(affects)

        etype, affects = classify_calendar_event("[RAPAT] Evaluasi Kurikulum Guru")
        self.assertEqual(etype, AcademicCalendarEventType.STAFF_MEETING)
        self.assertFalse(affects)

        etype, affects = classify_calendar_event("[ACARA] Pentas Seni Tahunan")
        self.assertEqual(etype, AcademicCalendarEventType.SCHOOL_EVENT)
        self.assertFalse(affects)

        # Prefixed tag
        etype, affects = classify_calendar_event("[EDUCORE:UJIAN] Asesmen Nasional")
        self.assertEqual(etype, AcademicCalendarEventType.EXAM)
        self.assertTrue(affects)

    def test_heuristic_keywords(self):
        etype, affects = classify_calendar_event("Ujian Akhir Semester Ganjil")
        self.assertEqual(etype, AcademicCalendarEventType.EXAM)
        self.assertTrue(affects)

        etype, affects = classify_calendar_event("Libur Bersama Idul Fitri")
        self.assertEqual(etype, AcademicCalendarEventType.HOLIDAY)
        self.assertTrue(affects)

        etype, affects = classify_calendar_event("Briefing guru sebelum kegiatan belajar")
        self.assertEqual(etype, AcademicCalendarEventType.STAFF_MEETING)
        self.assertFalse(affects)

        etype, affects = classify_calendar_event("Upacara Bendera Hari Senin")
        self.assertEqual(etype, AcademicCalendarEventType.SCHOOL_EVENT)
        self.assertFalse(affects)

    def test_custom_keywords_in_policy(self):
        policy = CalendarAcademicSyncPolicy(
            auto_sync_enabled=True,
            custom_keywords={"EXAM": ["tryout akbar", "evaluasi mingguan"]},
        )
        etype, affects = classify_calendar_event("Persiapan tryout akbar siswa", policy=policy)
        self.assertEqual(etype, AcademicCalendarEventType.EXAM)
        self.assertTrue(affects)

    def test_fallback_unclassified(self):
        etype, affects = classify_calendar_event("Diskusi santai di kantin")
        self.assertEqual(etype, AcademicCalendarEventType.OTHER)
        self.assertFalse(affects)


class CalendarAcademicMappingTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher_user = self.fx['teacher_user']
        set_current_foundation_id(self.foundation.id)

        # Create external calendar connection
        self.connection = CalendarConnection.objects.create(
            foundation_id=self.foundation.id,
            user=self.teacher_user,
            provider=CalendarConnection.PROVIDER_GOOGLE,
            provider_email="siti@cendekia.sch.id",
            access_token_encrypted=encrypt_secret("dummy-access-token"),
            status=CalendarConnection.STATUS_CONNECTED,
        )

    def _create_external_event(self, title, start_dt, end_dt, is_cancelled=False, is_all_day=False):
        return ExternalCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            connection=self.connection,
            provider_event_id=f"evt_{hash(title)}_{start_dt.timestamp()}",
            title=title,
            description="Detail acara kalender",
            location="Ruang Kelas 10A",
            start_at=start_dt,
            end_at=end_dt,
            is_all_day=is_all_day,
            is_cancelled=is_cancelled,
        )

    def test_opt_in_guardrail_default_disabled(self):
        # Default policy: auto_sync_enabled is False
        policy = CalendarAcademicSyncPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            auto_sync_enabled=False,
        )
        now = timezone.now()
        ext_event = self._create_external_event("[UJIAN] MTK UTS", now, now + datetime.timedelta(hours=2))

        # Mapping returns None without mutating data
        res = map_external_event_to_academic(ext_event, policy=policy, school=self.school)
        self.assertIsNone(res)
        self.assertEqual(AcademicCalendarEvent.objects.filter(foundation_id=self.foundation.id).count(), 0)

        # Sync service also reports 0 created and policy_enabled=False
        summary = sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        self.assertFalse(summary['policy_enabled'])
        self.assertEqual(summary['created'], 0)

    def test_dry_run_simulation(self):
        # Dry run allows previewing without saving to database even if disabled
        policy = CalendarAcademicSyncPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            auto_sync_enabled=False,
        )
        now = timezone.now()
        ext_event = self._create_external_event("[LIBUR] Maulid Nabi", now, now + datetime.timedelta(days=1))

        res = map_external_event_to_academic(ext_event, policy=policy, school=self.school, dry_run=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.event_type, AcademicCalendarEventType.HOLIDAY)
        self.assertTrue(res.affects_attendance)
        self.assertIsNone(res.id)  # not saved to DB
        self.assertEqual(AcademicCalendarEvent.objects.filter(foundation_id=self.foundation.id).count(), 0)

    def test_sync_enabled_creates_and_updates_idempotently(self):
        policy = CalendarAcademicSyncPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            auto_sync_enabled=True,
            auto_create_exams=False,
        )
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime(2026, 8, 10, 8, 0, 0), tz)
        end = timezone.make_aware(datetime.datetime(2026, 8, 10, 10, 0, 0), tz)
        ext_event = self._create_external_event("[RAPAT] Rapat Dewan Guru", start, end)

        # Run sync
        summary = sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['updated'], 0)

        event = AcademicCalendarEvent.objects.get(foundation_id=self.foundation.id, external_event=ext_event)
        self.assertEqual(event.event_type, AcademicCalendarEventType.STAFF_MEETING)
        self.assertFalse(event.affects_attendance)
        self.assertEqual(event.title, "[RAPAT] Rapat Dewan Guru")

        # Re-run sync (idempotency check)
        ext_event.title = "[RAPAT] Rapat Dewan Guru Semester Ganjil"
        ext_event.save()

        summary2 = sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        self.assertEqual(summary2['created'], 0)
        self.assertEqual(summary2['updated'], 1)
        event.refresh_from_db()
        self.assertEqual(event.title, "[RAPAT] Rapat Dewan Guru Semester Ganjil")

    def test_cancellation_propagation_soft_deletes_academic_event(self):
        CalendarAcademicSyncPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            auto_sync_enabled=True,
        )
        now = timezone.now()
        ext_event = self._create_external_event("[LIBUR] Libur Tambahan", now, now + datetime.timedelta(days=1))

        # Initial sync creates academic event
        sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        event = AcademicCalendarEvent.objects.get(foundation_id=self.foundation.id, external_event=ext_event)
        self.assertFalse(event.is_deleted)

        # External provider cancels event
        ext_event.is_cancelled = True
        ext_event.save()

        summary = sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        self.assertEqual(summary['cancelled'], 1)

        # AcademicCalendarEvent is soft-deleted, not physically deleted
        self.assertFalse(AcademicCalendarEvent.objects.filter(id=event.id).exists())
        soft_deleted = AcademicCalendarEvent.all_tenants.with_deleted().get(id=event.id)
        self.assertTrue(soft_deleted.is_deleted)

    def test_exam_draft_creation_opt_in(self):
        # 1. When auto_create_exams=False, no Exam model is created
        policy = CalendarAcademicSyncPolicy.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            auto_sync_enabled=True,
            auto_create_exams=False,
        )
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime(2026, 9, 15, 9, 0, 0), tz)
        end = timezone.make_aware(datetime.datetime(2026, 9, 15, 11, 0, 0), tz)
        ext_event = self._create_external_event("[UJIAN] MTK Ujian Tengah Semester", start, end)

        sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        event = AcademicCalendarEvent.objects.get(foundation_id=self.foundation.id, external_event=ext_event)
        self.assertEqual(event.event_type, AcademicCalendarEventType.EXAM)
        self.assertIsNone(event.exam)
        self.assertEqual(Exam.objects.filter(foundation_id=self.foundation.id).count(), 0)

        # 2. When auto_create_exams=True, Exam draft is created and linked
        policy.auto_create_exams = True
        policy.save()

        sync_external_calendar_events_to_academic(self.foundation.id, school_id=self.school.id)
        event.refresh_from_db()
        self.assertIsNotNone(event.exam)
        self.assertEqual(event.exam.class_subject, self.fx['class_subject'])
        self.assertFalse(event.exam.published)  # Draft safety guardrail
        self.assertEqual(event.exam.duration_min, 120)


class TimetableAttendanceExemptionTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)

        # Monday August 10, 2026
        self.test_date = datetime.date(2026, 8, 10)  # isoweekday=1 (Monday)

        # Create a timetable slot on Monday period 1 (08:00 - 09:30)
        self.slot = TimetableSlot.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.fx['class_subject'],
            day_of_week=1,
            period_no=1,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(9, 30),
            room="Lab Komputer 1",
        )

    def test_teacher_agenda_exemption_annotation(self):
        # Without any calendar event, slot is not exempt
        agenda = get_teacher_agenda(self.teacher, self.test_date)
        self.assertEqual(len(agenda), 1)
        self.assertFalse(agenda[0]['is_exempt'])
        self.assertIsNone(agenda[0]['exemption_reason'])

        # Add an all-day holiday event affecting attendance
        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(datetime.datetime(2026, 8, 10, 0, 0, 0), tz)
        day_end = timezone.make_aware(datetime.datetime(2026, 8, 10, 23, 59, 59), tz)

        holiday = AcademicCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            event_type=AcademicCalendarEventType.HOLIDAY,
            title="Hari Libur Daerah",
            start_at=day_start,
            end_at=day_end,
            is_all_day=True,
            affects_attendance=True,
        )

        agenda_exempt = get_teacher_agenda(self.teacher, self.test_date)
        self.assertEqual(len(agenda_exempt), 1)
        self.assertTrue(agenda_exempt[0]['is_exempt'])
        self.assertEqual(agenda_exempt[0]['exemption_reason'], "Hari Libur Daerah")
        self.assertEqual(agenda_exempt[0]['calendar_event_id'], holiday.id)

    def test_expected_periods_for_school_exemption_and_missing_filter(self):
        # 1. Before holiday: slot is expected and present in missing_only=True
        expected = get_expected_periods_for_school(self.school, self.test_date, missing_only=False)
        self.assertEqual(len(expected), 1)
        self.assertFalse(expected[0]['is_exempt'])

        missing = get_expected_periods_for_school(self.school, self.test_date, missing_only=True)
        self.assertEqual(len(missing), 1)

        # 2. Add timetable exception event
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.datetime(2026, 8, 10, 8, 0, 0), tz)
        end = timezone.make_aware(datetime.datetime(2026, 8, 10, 10, 0, 0), tz)

        AcademicCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            event_type=AcademicCalendarEventType.TIMETABLE_EXCEPTION,
            title="Pengecualian Jadwal Upacara Kemerdekaan",
            start_at=start,
            end_at=end,
            affects_attendance=True,
        )

        expected_after = get_expected_periods_for_school(self.school, self.test_date, missing_only=False)
        self.assertEqual(len(expected_after), 1)
        self.assertTrue(expected_after[0]['is_exempt'])
        self.assertEqual(expected_after[0]['exemption_reason'], "Pengecualian Jadwal Upacara Kemerdekaan")

        # In missing_only=True, exempt slots are excluded to avoid false alerts!
        missing_after = get_expected_periods_for_school(self.school, self.test_date, missing_only=True)
        self.assertEqual(len(missing_after), 0)


class CalendarAcademicApiTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher_user = self.fx['teacher_user']
        set_current_foundation_id(self.foundation.id)

        # Grant admin / school_config permission
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id,
            user=self.teacher_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher_user)
        self.client.defaults['HTTP_X_FOUNDATION_ID'] = str(self.foundation.id)

    def test_calendar_events_crud_api(self):
        # Create event via API
        payload = {
            'school': self.school.id,
            'event_type': AcademicCalendarEventType.HOLIDAY,
            'title': 'Cuti Bersama Akhir Tahun',
            'start_at': '2026-12-24T00:00:00Z',
            'end_at': '2026-12-25T23:59:59Z',
            'is_all_day': True,
            'affects_attendance': True,
        }
        res = self.client.post('/api/v1/academic/calendar-events/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        event_id = res.data['id']

        # List events
        res_list = self.client.get('/api/v1/academic/calendar-events/')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_list.data['results']), 1)

        # Retrieve single event
        res_detail = self.client.get(f'/api/v1/academic/calendar-events/{event_id}/')
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_detail.data['title'], 'Cuti Bersama Akhir Tahun')

    def test_calendar_sync_policy_api(self):
        # Configure policy via API
        payload = {
            'school': self.school.id,
            'auto_sync_enabled': True,
            'auto_create_exams': True,
            'tag_prefix': '[EDUCORE]',
            'custom_keywords': {'EXAM': ['tes formatif']},
        }
        res = self.client.post('/api/v1/academic/calendar-sync-policies/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['auto_sync_enabled'])
        self.assertTrue(res.data['auto_create_exams'])

    def test_calendar_sync_trigger_api(self):
        # Trigger sync endpoint with dry_run
        res = self.client.post('/api/v1/academic/calendar-events/sync/', {'school_id': self.school.id, 'dry_run': True}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['dry_run'])
        self.assertIn('total_scanned', res.data)

    def test_multitenant_isolation(self):
        # Create event in foundation A
        tz = timezone.get_current_timezone()
        now = timezone.make_aware(datetime.datetime(2026, 8, 1, 8, 0, 0), tz)
        AcademicCalendarEvent.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            title="Acara Yayasan A",
            start_at=now,
            end_at=now + datetime.timedelta(hours=2),
        )

        # Foundation B setup
        fx_b = build_academic_fixture("Yayasan Mitra Mandiri")
        foundation_b = fx_b['foundation']
        user_b = fx_b['teacher_user']
        RoleAssignment.objects.create(
            foundation_id=foundation_b.id,
            user=user_b,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=fx_b['school'].id,
        )

        client_b = APIClient()
        client_b.force_authenticate(user=user_b)
        client_b.defaults['HTTP_X_FOUNDATION_ID'] = str(foundation_b.id)

        res_b = client_b.get('/api/v1/academic/calendar-events/')
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_b.data['results']), 0)  # Cannot see foundation A's event

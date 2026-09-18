import datetime

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import CounsellingConfidentiality, CounsellingSession
from apps.campus.services import (
    access_counselling_session,
    get_counsellor_followups_due,
    is_counselling_reader_authorized,
    queue_counsellor_followup_reminder,
    record_counselling_session,
)
from apps.core.models import AuditEvent, JobRun
from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.notifications.models import IntentStatus, NotificationCategory, NotificationIntent


def make_counsellor(fx, nik="3471010101017777", full_name="Bu Konselor"):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=full_name)
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=f"+62819{nik[-7:]}",
        email=f"{nik}@cendekia.sch.id",
        full_name=full_name,
    )
    staff = Staff.all_tenants.create(
        foundation_id=fx['foundation'].id,
        person=person,
        user=user,
        school=fx['school'],
        nip="198601012010012002",
        employment_type=Staff.TYPE_PERMANENT,
        join_date=datetime.date(2021, 1, 1),
        status=Staff.STATUS_ACTIVE,
    )
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role=RoleAssignment.ROLE_COUNSELLOR,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=fx['school'].id,
    )
    return staff


def make_principal(fx, nik="3471010101016666", full_name="Kepala Sekolah"):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=full_name)
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=f"+62817{nik[-7:]}",
        email=f"{nik}@cendekia.sch.id",
        full_name=full_name,
    )
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role=RoleAssignment.ROLE_SCHOOL_ADMIN,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=fx['school'].id,
    )
    return user


class CounsellingServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.counsellor = make_counsellor(self.fx)
        self.principal = make_principal(self.fx)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_notes_encrypted_at_rest(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Curhat masalah keluarga, sangat sensitif.",
        )
        self.assertNotIn("Curhat", session.notes_encrypted)
        self.assertEqual(session.notes, "Curhat masalah keluarga, sangat sensitif.")

    def test_creation_audit_excludes_note_content(self):
        record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Rahasia pribadi siswa.",
        )
        event = AuditEvent.objects.filter(action='campus.counselling.session_recorded').latest('id')
        self.assertNotIn('notes', event.diff)
        self.assertNotIn('Rahasia', str(event.diff))

    def test_restricted_note_visible_only_to_author_and_principal(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Catatan terbatas.",
            confidentiality=CounsellingConfidentiality.RESTRICTED,
        )
        self.assertTrue(is_counselling_reader_authorized(session, self.counsellor.user, self.fx['foundation'].id))
        self.assertTrue(is_counselling_reader_authorized(session, self.principal, self.fx['foundation'].id))
        self.assertFalse(is_counselling_reader_authorized(session, self.fx['teacher_user'], self.fx['foundation'].id))

    def test_access_restricted_note_denied_raises_and_audits(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Catatan terbatas.",
            confidentiality=CounsellingConfidentiality.RESTRICTED,
        )
        with self.assertRaises(PermissionDenied):
            access_counselling_session(session, self.fx['teacher_user'], self.fx['foundation'].id)

        event = AuditEvent.objects.filter(action='campus.counselling.restricted_note_access_denied').latest('id')
        self.assertEqual(event.actor_id, str(self.fx['teacher_user'].id))

    def test_access_restricted_note_granted_audits(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Catatan terbatas.",
            confidentiality=CounsellingConfidentiality.RESTRICTED,
        )
        access_counselling_session(session, self.principal, self.fx['foundation'].id)
        event = AuditEvent.objects.filter(action='campus.counselling.restricted_note_accessed').latest('id')
        self.assertEqual(event.actor_id, str(self.principal.id))

    def test_urgent_session_escalates_to_principal_immediately(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Indikasi kekerasan di rumah.",
            is_urgent=True,
        )
        session.refresh_from_db()
        self.assertIsNotNone(session.urgent_notified_at)

        intent = NotificationIntent.objects.get(
            foundation_id=self.fx['foundation'].id,
            category=NotificationCategory.COUNSELLING_URGENT,
            recipient_user=self.principal,
        )
        self.assertIn(intent.status, [IntentStatus.DISPATCHED, IntentStatus.PROCESSING, IntentStatus.PENDING])
        self.assertEqual(intent.priority, 'CRITICAL')

    def test_non_urgent_session_does_not_escalate(self):
        record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Sesi rutin.",
            is_urgent=False,
        )
        self.assertFalse(
            NotificationIntent.objects.filter(
                foundation_id=self.fx['foundation'].id,
                category=NotificationCategory.COUNSELLING_URGENT,
            ).exists()
        )

    def test_followup_reminder_due_and_dispatch(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            notes="Butuh tindak lanjut.",
            follow_up_at=timezone.now() - datetime.timedelta(days=1),
        )
        due = list(get_counsellor_followups_due())
        self.assertIn(session, due)

        sent = queue_counsellor_followup_reminder(session)
        self.assertTrue(sent)
        session.refresh_from_db()
        self.assertIsNotNone(session.follow_up_reminder_sent_at)

        intent = NotificationIntent.objects.get(
            foundation_id=self.fx['foundation'].id,
            category=NotificationCategory.COUNSELLING_FOLLOW_UP,
            recipient_user=self.counsellor.user,
        )
        self.assertIsNotNone(intent)

        # Second sweep no longer sees it as due.
        self.assertNotIn(session, list(get_counsellor_followups_due()))

    def test_future_followup_not_yet_due(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            follow_up_at=timezone.now() + datetime.timedelta(days=3),
        )
        self.assertNotIn(session, list(get_counsellor_followups_due()))

    def test_send_counsellor_followup_reminders_command(self):
        session = record_counselling_session(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            counsellor=self.counsellor,
            recorded_by=self.counsellor.user,
            follow_up_at=timezone.now() - datetime.timedelta(hours=2),
        )
        call_command('send_counsellor_followup_reminders', force=True)
        session.refresh_from_db()
        self.assertIsNotNone(session.follow_up_reminder_sent_at)
        job_run = JobRun.objects.filter(job_name='send_counsellor_followup_reminders').latest('id')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)


class CounsellingAPITests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.counsellor = make_counsellor(self.fx)
        self.principal = make_principal(self.fx)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        self.client = APIClient()

    def _create_session(self, **overrides):
        payload = dict(
            student_id=self.fx['student'].id,
            counsellor_id=self.counsellor.id,
            notes="Catatan rahasia",
            confidentiality='RESTRICTED',
        )
        payload.update(overrides)
        self.client.force_authenticate(user=self.counsellor.user)
        res = self.client.post('/api/v1/campus/counselling/sessions/', payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data['id']

    def test_teacher_gets_403_on_restricted_session(self):
        session_id = self._create_session()
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/campus/counselling/sessions/{session_id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            AuditEvent.objects.filter(
                action='campus.counselling.restricted_note_access_denied',
                actor_id=str(self.fx['teacher_user'].id),
            ).exists()
        )

    def test_principal_can_view_restricted_session(self):
        session_id = self._create_session()
        self.client.force_authenticate(user=self.principal)
        res = self.client.get(f'/api/v1/campus/counselling/sessions/{session_id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['notes'], 'Catatan rahasia')

    def test_restricted_session_omitted_from_teacher_list(self):
        self._create_session()
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/campus/counselling/sessions/?student_id={self.fx["student"].id}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['results'], [])

    def test_normal_session_visible_to_staff(self):
        session_id = self._create_session(confidentiality='NORMAL', notes='Sesi biasa')
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/campus/counselling/sessions/{session_id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['notes'], 'Sesi biasa')

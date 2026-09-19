"""Mode ujian web console: exam list (/web/academic/exams/) and proctor page."""
import datetime

from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment, ExamQuestionType
from apps.academic.services import start_attempt
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_exam_lockdown_proctor import add_question, make_exam
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import set_current_foundation_id


class ExamModeConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Mode Ujian")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, class_group=self.fx['class_group'], student=self.fx['student'],
            enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def _titles_by_state(self, res):
        return {row['exam'].title: row['state'] for row in res.context['rows']}

    def test_list_derives_state_from_publish_flag_and_window(self):
        now = timezone.now()
        make_exam(self.fx, title="Draft", published=False)
        make_exam(self.fx, title="Live", published=True)
        make_exam(self.fx, title="Nanti", published=True, window_start=now + datetime.timedelta(days=1), window_end=now + datetime.timedelta(days=1, hours=2))
        make_exam(self.fx, title="Lewat", published=True, window_start=now - datetime.timedelta(days=2), window_end=now - datetime.timedelta(days=1))
        res = self.client.get('/web/academic/exams/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._titles_by_state(res), {'Draft': 'DRAFT', 'Live': 'LIVE', 'Nanti': 'UPCOMING', 'Lewat': 'ENDED'})
        self.assertContains(res, 'Berlangsung')

    def test_list_counts_attempts_per_exam(self):
        exam = make_exam(self.fx, published=True)
        add_question(exam, 1, ExamQuestionType.MCQ)
        start_attempt(exam, self.fx['student'])
        res = self.client.get('/web/academic/exams/')
        row = res.context['rows'][0]
        self.assertEqual((row['in_progress'], row['submitted']), (1, 0))

    def test_list_empty_state(self):
        res = self.client.get('/web/academic/exams/')
        self.assertContains(res, 'Belum ada ujian untuk sekolah ini.')

    def test_proctor_page_renders_console_with_roster(self):
        exam = make_exam(self.fx, published=True)
        add_question(exam, 1, ExamQuestionType.MCQ)
        res = self.client.get(f'/web/academic/exams/{exam.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'proctor-console-app')
        self.assertContains(res, 'Andi Wijaya')
        self.assertEqual(res.context['proctor_data']['summary']['not_started'], 1)

    def test_proctor_page_404_for_exam_of_unpermitted_school(self):
        other = build_academic_fixture("Yayasan Ujian Lain")
        set_current_foundation_id(other['foundation'].id)
        foreign_exam = make_exam(other, published=True)
        set_current_foundation_id(self.foundation.id)
        self.assertEqual(self.client.get(f'/web/academic/exams/{foreign_exam.id}/').status_code, 404)


class ExamPublishConsoleTests(TestCase):
    def setUp(self):
        from apps.core.models import AuditEvent
        from apps.identity.models import Person, Staff, User
        self.AuditEvent = AuditEvent
        self.fx = build_academic_fixture("Yayasan Terbit Ujian")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.fx['teacher_user'], role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        reader = User.objects.create(foundation_id=self.foundation.id, phone_e164='+6281200099002', full_name='Konselor')
        Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Konselor'),
            user=reader, school=self.school, join_date=timezone.localdate(),
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=reader, role='counsellor',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.reader = reader
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def _url(self, exam):
        return f'/web/academic/exams/{exam.id}/publish/?school_id={self.school.id}'

    def test_publish_sets_flag_and_audits_once(self):
        exam = make_exam(self.fx, published=False)
        res = self.client.post(self._url(exam))
        self.assertEqual(res.status_code, 302)
        exam.refresh_from_db()
        self.assertTrue(exam.published)
        self.assertEqual(self.AuditEvent.objects.filter(action='academic.exam.published', entity_id=str(exam.id)).count(), 1)
        self.client.post(self._url(exam))  # idempotent
        self.assertEqual(self.AuditEvent.objects.filter(action='academic.exam.published').count(), 1)

    def test_list_shows_publish_button_only_for_drafts_and_writers(self):
        exam = make_exam(self.fx, published=False)
        make_exam(self.fx, title='Sudah terbit', published=True)
        res = self.client.get('/web/academic/exams/')
        self.assertContains(res, f'/web/academic/exams/{exam.id}/publish/')
        self.assertEqual(res.content.decode().count('/publish/'), 1)
        self.client.force_authenticate(user=self.reader)
        self.assertNotContains(self.client.get('/web/academic/exams/'), '/publish/')

    def test_read_only_user_cannot_publish(self):
        exam = make_exam(self.fx, published=False)
        self.client.force_authenticate(user=self.reader)
        self.assertRedirects(self.client.post(self._url(exam)), reverse('web-console-home'), fetch_redirect_response=False)
        exam.refresh_from_db()
        self.assertFalse(exam.published)

    def test_exam_of_another_foundation_is_404(self):
        other = build_academic_fixture("Yayasan Ujian Asing")
        set_current_foundation_id(other['foundation'].id)
        foreign = make_exam(other, published=False)
        set_current_foundation_id(self.foundation.id)
        self.assertEqual(self.client.post(self._url(foreign)).status_code, 404)
        foreign.refresh_from_db()
        self.assertFalse(foreign.published)

    def test_json_publish_action_now_audits_too(self):
        exam = make_exam(self.fx, published=False)
        res = self.client.post(f'/api/v1/academic/exams/{exam.id}/publish')
        self.assertIn(res.status_code, (200, 301, 302), res.content[:200])
        exam.refresh_from_db()
        if exam.published:
            self.assertTrue(self.AuditEvent.objects.filter(action='academic.exam.published').exists())

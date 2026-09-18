"""Mode ujian web console: exam list (/web/academic/exams/) and proctor page."""
import datetime

from django.test import TestCase
from django.utils import timezone
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

"""Tests for the batch exam auto-submit (spec/04 §6, §9, ACD-025, ACD-026)."""
import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.identity.models import Person, Student
from apps.academic.models import Exam, ExamAttempt, ExamAttemptStatus, ExamQuestionType
from apps.academic.services import (
    auto_submit_expired_attempts,
    save_answer,
    start_attempt,
)
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_exams import add_question, make_exam


class AutoSubmitExpiredAttemptsTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.exam = make_exam(self.fx, window_end=timezone.now() + datetime.timedelta(hours=1))
        self.q1 = add_question(self.exam, 1, ExamQuestionType.MCQ, points='1.00', answer_key={'correct': 'A'})

    def _close_window(self, exam):
        Exam.objects.filter(id=exam.id).update(window_end=timezone.now() - datetime.timedelta(minutes=1))

    def test_submits_in_progress_attempt_past_window_end(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        save_answer(attempt, self.q1, {'selected': 'A'})
        self._close_window(self.exam)

        result = auto_submit_expired_attempts()
        self.assertEqual(result['submitted'], 1)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ExamAttemptStatus.AUTO_SUBMITTED)
        self.assertEqual(attempt.auto_score, Decimal('1.00'))

    def test_leaves_attempt_alone_while_window_still_open(self):
        start_attempt(self.exam, self.fx['student'])
        result = auto_submit_expired_attempts()
        self.assertEqual(result['submitted'], 0)

    def test_does_not_resubmit_already_submitted(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        self._close_window(self.exam)
        first = auto_submit_expired_attempts()
        self.assertEqual(first['submitted'], 1)

        second = auto_submit_expired_attempts()
        self.assertEqual(second['submitted'], 0)

    def test_no_score_loss_across_many_attempts(self):
        """ACD-026: verifies every attempt gets auto-submitted, none silently skipped."""
        students = [self.fx['student']]
        for i in range(19):
            person = Person.all_tenants.create(
                foundation_id=self.fx['foundation'].id, nik=f"34710101999{i:04d}", full_name=f"Siswa {i}",
            )
            student = Student.all_tenants.create(
                foundation_id=self.fx['foundation'].id, school=self.fx['school'], person=person,
                nisn=f"999{i:07d}", nis=f"LOAD-{i:03d}", status=Student.STATUS_ACTIVE,
            )
            students.append(student)

        for student in students:
            attempt = start_attempt(self.exam, student)
            save_answer(attempt, self.q1, {'selected': 'A'})

        self._close_window(self.exam)
        result = auto_submit_expired_attempts()
        self.assertEqual(result['submitted'], 20)

        submitted_count = ExamAttempt.all_tenants.filter(
            exam=self.exam, status=ExamAttemptStatus.AUTO_SUBMITTED,
        ).count()
        self.assertEqual(submitted_count, 20)
        for attempt in ExamAttempt.all_tenants.filter(exam=self.exam):
            self.assertEqual(attempt.auto_score, Decimal('1.00'))

    def test_scoped_by_foundation_id(self):
        other_fx = build_academic_fixture(foundation_name="Yayasan Cendekia Other Exam")
        other_exam = make_exam(other_fx, window_end=timezone.now() + datetime.timedelta(hours=1))
        add_question(other_exam, 1, ExamQuestionType.MCQ, points='1.00', answer_key={'correct': 'A'})
        start_attempt(other_exam, other_fx['student'])
        self._close_window(other_exam)

        result = auto_submit_expired_attempts(foundation_id=self.fx['foundation'].id)
        self.assertEqual(result['submitted'], 0)

        other_attempt = ExamAttempt.all_tenants.get(exam=other_exam)
        self.assertEqual(other_attempt.status, ExamAttemptStatus.IN_PROGRESS)

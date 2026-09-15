import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.academic.models import (
    Exam,
    ExamAnswer,
    ExamAttemptStatus,
    ExamMode,
    ExamQuestion,
    ExamQuestionType,
)
from apps.academic.services import (
    AttemptAlreadySubmittedError,
    ExamWindowError,
    auto_grade_answer,
    auto_submit_if_expired,
    compute_remaining_seconds,
    grade_essay_answer,
    record_focus_loss,
    save_answer,
    start_attempt,
    submit_attempt,
)
from apps.academic.tests.base import build_academic_fixture


def make_exam(fx, **overrides):
    now = timezone.now()
    defaults = dict(
        foundation_id=fx['foundation'].id,
        class_subject=fx['class_subject'],
        title="UAS Matematika",
        mode=ExamMode.ONLINE,
        window_start=now - datetime.timedelta(minutes=5),
        window_end=now + datetime.timedelta(hours=2),
        duration_min=60,
    )
    defaults.update(overrides)
    return Exam.objects.create(**defaults)


def add_question(exam, seq, qtype, points='1.00', answer_key=None, options=None):
    return ExamQuestion.objects.create(
        foundation_id=exam.foundation_id,
        exam=exam,
        seq=seq,
        type=qtype,
        body=f"Question {seq}",
        options=options or [],
        points=Decimal(points),
        answer_key=answer_key or {},
    )


class AutoGradingTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.exam = make_exam(self.fx)

    def test_mcq_correct_and_incorrect(self):
        q = add_question(self.exam, 1, ExamQuestionType.MCQ, points='2.00', answer_key={'correct': 'A'})
        self.assertEqual(auto_grade_answer(q, {'selected': 'A'}), Decimal('2.00'))
        self.assertEqual(auto_grade_answer(q, {'selected': 'B'}), Decimal('0.00'))

    def test_true_false(self):
        q = add_question(self.exam, 2, ExamQuestionType.TRUE_FALSE, points='1.00', answer_key={'correct': True})
        self.assertEqual(auto_grade_answer(q, {'selected': True}), Decimal('1.00'))
        self.assertEqual(auto_grade_answer(q, {'selected': False}), Decimal('0.00'))

    def test_short_normalized_match(self):
        q = add_question(self.exam, 3, ExamQuestionType.SHORT, points='1.00', answer_key={'accepted': ['Jakarta', 'DKI Jakarta']})
        self.assertEqual(auto_grade_answer(q, {'text': '  jakarta '}), Decimal('1.00'))
        self.assertEqual(auto_grade_answer(q, {'text': 'Bandung'}), Decimal('0.00'))

    def test_matching_exact_pairs(self):
        q = add_question(self.exam, 4, ExamQuestionType.MATCHING, points='1.00', answer_key={'pairs': {'1': 'B', '2': 'A'}})
        self.assertEqual(auto_grade_answer(q, {'pairs': {'1': 'B', '2': 'A'}}), Decimal('1.00'))
        self.assertEqual(auto_grade_answer(q, {'pairs': {'1': 'A', '2': 'B'}}), Decimal('0.00'))

    def test_multi_exact_no_partial_credit(self):
        q = add_question(self.exam, 5, ExamQuestionType.MULTI, points='4.00', answer_key={'correct': ['A', 'C'], 'partial_credit': False})
        self.assertEqual(auto_grade_answer(q, {'selected': ['A', 'C']}), Decimal('4.00'))
        self.assertEqual(auto_grade_answer(q, {'selected': ['A']}), Decimal('0.00'))

    def test_multi_partial_credit(self):
        q = add_question(self.exam, 6, ExamQuestionType.MULTI, points='4.00', answer_key={'correct': ['A', 'B'], 'partial_credit': True})
        self.assertEqual(auto_grade_answer(q, {'selected': ['A']}), Decimal('2.00'))
        self.assertEqual(auto_grade_answer(q, {'selected': ['A', 'B', 'C']}), Decimal('2.00'))

    def test_essay_not_auto_graded(self):
        q = add_question(self.exam, 7, ExamQuestionType.ESSAY, points='5.00')
        self.assertIsNone(auto_grade_answer(q, {'text': 'free response'}))


class AttemptLifecycleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.exam = make_exam(self.fx)
        self.q1 = add_question(self.exam, 1, ExamQuestionType.MCQ, points='1.00', answer_key={'correct': 'A'})
        self.q2 = add_question(self.exam, 2, ExamQuestionType.ESSAY, points='3.00')

    def test_start_attempt_outside_window_rejected(self):
        closed_exam = make_exam(
            self.fx,
            window_start=timezone.now() - datetime.timedelta(days=2),
            window_end=timezone.now() - datetime.timedelta(days=1),
        )
        with self.assertRaises(ExamWindowError):
            start_attempt(closed_exam, self.fx['student'])

    def test_start_attempt_resumes_existing(self):
        a1 = start_attempt(self.exam, self.fx['student'])
        a2 = start_attempt(self.exam, self.fx['student'])
        self.assertEqual(a1.id, a2.id)

    def test_remaining_seconds_positive_after_start(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        remaining = compute_remaining_seconds(attempt)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 60 * 60)

    def test_save_answer_persists_and_autogrades(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        record = save_answer(attempt, self.q1, {'selected': 'A'})
        self.assertEqual(record.points_awarded, Decimal('1.00'))

    def test_save_answer_on_submitted_attempt_rejected(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        submit_attempt(attempt)
        with self.assertRaises(AttemptAlreadySubmittedError):
            save_answer(attempt, self.q1, {'selected': 'A'})

    def test_submit_pending_essay_leaves_final_score_none(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        save_answer(attempt, self.q1, {'selected': 'A'})
        submitted = submit_attempt(attempt)
        self.assertEqual(submitted.status, ExamAttemptStatus.SUBMITTED)
        self.assertEqual(submitted.auto_score, Decimal('1.00'))
        self.assertIsNone(submitted.final_score)

    def test_grade_essay_completes_final_score(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        save_answer(attempt, self.q1, {'selected': 'A'})
        essay_answer = save_answer(attempt, self.q2, {'text': 'my essay'})
        submit_attempt(attempt)

        graded = grade_essay_answer(essay_answer, Decimal('2.50'))
        self.assertEqual(graded.points_awarded, Decimal('2.50'))
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_score, Decimal('3.50'))  # 1.00 auto + 2.50 manual

    def test_auto_submit_when_window_expired(self):
        exam = make_exam(
            self.fx,
            window_start=timezone.now() - datetime.timedelta(minutes=10),
            window_end=timezone.now() + datetime.timedelta(seconds=1),
            duration_min=60,
        )
        attempt = start_attempt(exam, self.fx['student'])
        attempt.started_at = timezone.now() - datetime.timedelta(hours=2)
        attempt.save()
        result = auto_submit_if_expired(attempt)
        self.assertEqual(result.status, ExamAttemptStatus.AUTO_SUBMITTED)

    def test_focus_loss_increments_counter_never_punitive(self):
        attempt = start_attempt(self.exam, self.fx['student'])
        record_focus_loss(attempt)
        record_focus_loss(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.focus_loss_count, 2)
        self.assertEqual(attempt.status, ExamAttemptStatus.IN_PROGRESS)


class ExamViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        self.exam = make_exam(self.fx)
        self.q1 = add_question(self.exam, 1, ExamQuestionType.MCQ, points='1.00', answer_key={'correct': 'A'})

    def test_start_attempt_via_api_hides_answer_key(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post(f'/api/v1/academic/exams/{self.exam.id}/attempts/', {
            'student_id': self.fx['student'].id,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertIn('remaining_seconds', body)
        self.assertNotIn('answer_key', body['questions'][0])

    def test_answer_and_submit_flow(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_start = self.client.post(f'/api/v1/academic/exams/{self.exam.id}/attempts/', {
            'student_id': self.fx['student'].id,
        }, format='json')
        attempt_id = res_start.json()['attempt']['id']

        res_answer = self.client.patch(f'/api/v1/academic/exam-attempts/{attempt_id}/answers/', {
            'question_id': self.q1.id, 'answer': {'selected': 'A'},
        }, format='json')
        self.assertEqual(res_answer.status_code, 200, res_answer.content)

        res_submit = self.client.post(f'/api/v1/academic/exam-attempts/{attempt_id}/submit/')
        self.assertEqual(res_submit.status_code, 200)
        self.assertEqual(res_submit.json()['status'], ExamAttemptStatus.SUBMITTED)
        self.assertEqual(res_submit.json()['auto_score'], '1.00')

    def test_grading_queue_lists_pending_essays(self):
        essay_q = add_question(self.exam, 2, ExamQuestionType.ESSAY, points='5.00')
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_start = self.client.post(f'/api/v1/academic/exams/{self.exam.id}/attempts/', {
            'student_id': self.fx['student'].id,
        }, format='json')
        attempt_id = res_start.json()['attempt']['id']
        self.client.patch(f'/api/v1/academic/exam-attempts/{attempt_id}/answers/', {
            'question_id': essay_q.id, 'answer': {'text': 'response'},
        }, format='json')
        self.client.post(f'/api/v1/academic/exam-attempts/{attempt_id}/submit/')

        res_queue = self.client.get(f'/api/v1/academic/exams/{self.exam.id}/grading-queue/')
        self.assertEqual(res_queue.status_code, 200)
        self.assertEqual(len(res_queue.json()), 1)

        res_grade = self.client.post(f'/api/v1/academic/exam-attempts/{attempt_id}/grade-answer/', {
            'question_id': essay_q.id, 'points': '4.00',
        }, format='json')
        self.assertEqual(res_grade.status_code, 200, res_grade.content)

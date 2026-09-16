import datetime
from decimal import Decimal
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.identity.models import (
    Foundation,
    Person,
    RoleAssignment,
    School,
    Staff,
    Student,
    User,
)
from apps.academic.models import (
    ClassEnrollment,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamAttemptStatus,
    ExamMode,
    ExamQuestion,
    ExamQuestionType,
)
from apps.academic.services import (
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
        title="Ujian Akhir Semester Fisika",
        mode=ExamMode.ONLINE,
        window_start=now - datetime.timedelta(minutes=10),
        window_end=now + datetime.timedelta(hours=2),
        duration_min=90,
        settings={
            'one_question_at_a_time': True,
            'block_back_navigation': False,
            'full_screen_lock': True,
        },
    )
    defaults.update(overrides)
    return Exam.objects.create(**defaults)


def add_question(exam, seq, qtype, points='1.00', answer_key=None, options=None):
    return ExamQuestion.objects.create(
        foundation_id=exam.foundation_id,
        exam=exam,
        seq=seq,
        type=qtype,
        body=f"Soal Nomor {seq}: Pertanyaan Fisika",
        options=options or [{'key': 'A', 'text': 'Pilihan A'}, {'key': 'B', 'text': 'Pilihan B'}],
        points=Decimal(points),
        answer_key=answer_key or {'correct': 'A'},
    )


class ExamLockdownAndProctorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher_user = self.fx['teacher_user']

        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.teacher_user,
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
        )

        self.exam = make_exam(self.fx)
        self.q1 = add_question(self.exam, 1, ExamQuestionType.MCQ, points='2.00', answer_key={'correct': 'A'})
        self.q2 = add_question(self.exam, 2, ExamQuestionType.MCQ, points='3.00', answer_key={'correct': 'B'})

        # Student 1: already in fx['student'] (enrolled in class group)
        self.s1 = self.fx['student']

        # Student 2: Create second student in same class
        p2 = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Budi Pratama",
            nik="3171010000000002",
            dob=datetime.date(2008, 2, 2),
            gender='L',
        )
        self.s2 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=p2,
            nis="2026002",
            status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id,
            class_group=self.fx['class_group'],
            student=self.s2,
            enrolled_at=datetime.date.today(),
            is_active=True,
        )

        # Student 3: Create third student in same class (Not started)
        p3 = Person.objects.create(
            foundation_id=self.foundation.id,
            full_name="Citra Lestari",
            nik="3171010000000003",
            dob=datetime.date(2008, 3, 3),
            gender='P',
        )
        self.s3 = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=p3,
            nis="2026003",
            status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id,
            class_group=self.fx['class_group'],
            student=self.s3,
            enrolled_at=datetime.date.today(),
            is_active=True,
        )

    def test_proctor_console_endpoint_metrics(self):
        """GET /api/v1/academic/exams/:id/proctor/ returns 6-column metrics and KPIs."""
        # Setup S1: In progress, 1 answer, 1 focus loss
        att1 = start_attempt(self.exam, self.s1)
        save_answer(att1, self.q1, {'selected': 'A'})
        record_focus_loss(att1)

        # Setup S2: Submitted, 3 focus losses (needs attention)
        att2 = start_attempt(self.exam, self.s2)
        save_answer(att2, self.q1, {'selected': 'A'})
        save_answer(att2, self.q2, {'selected': 'B'})
        record_focus_loss(att2)
        record_focus_loss(att2)
        record_focus_loss(att2)
        submit_attempt(att2)

        # S3 has no attempt (NOT_STARTED)

        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.get(f'/api/v1/academic/exams/{self.exam.id}/proctor/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)

        data = response.json()

        # Check exam header
        self.assertEqual(data['exam']['id'], self.exam.id)
        self.assertEqual(data['exam']['title'], self.exam.title)
        self.assertEqual(data['exam']['total_questions'], 2)
        self.assertTrue(data['exam']['is_active'])

        # Check summary KPIs
        self.assertEqual(data['summary']['total_students'], 3)
        self.assertEqual(data['summary']['in_progress'], 1)
        self.assertEqual(data['summary']['submitted'], 1)
        self.assertEqual(data['summary']['not_started'], 1)
        self.assertEqual(data['summary']['flagged_focus_loss'], 1)  # S2 has 3 focus losses

        # Check students roster
        students_by_nis = {s['nis']: s for s in data['students']}
        self.assertEqual(len(students_by_nis), 3)

        s1_row = students_by_nis[self.s1.nis]
        self.assertEqual(s1_row['status'], ExamAttemptStatus.IN_PROGRESS)
        self.assertEqual(s1_row['answered_count'], 1)
        self.assertEqual(s1_row['total_questions'], 2)
        self.assertEqual(s1_row['progress_pct'], 50)
        self.assertEqual(s1_row['focus_loss_count'], 1)
        self.assertFalse(s1_row['needs_attention'])
        self.assertIsNotNone(s1_row['remaining_seconds'])
        self.assertIsNotNone(s1_row['last_saved_at'])

        s2_row = students_by_nis[self.s2.nis]
        self.assertEqual(s2_row['status'], ExamAttemptStatus.SUBMITTED)
        self.assertEqual(s2_row['answered_count'], 2)
        self.assertEqual(s2_row['progress_pct'], 100)
        self.assertEqual(s2_row['focus_loss_count'], 3)
        self.assertTrue(s2_row['needs_attention'])
        self.assertEqual(s2_row['remaining_seconds'], 0)

        s3_row = students_by_nis[self.s3.nis]
        self.assertEqual(s3_row['status'], 'NOT_STARTED')
        self.assertEqual(s3_row['answered_count'], 0)
        self.assertEqual(s3_row['progress_pct'], 0)
        self.assertEqual(s3_row['focus_loss_count'], 0)
        self.assertFalse(s3_row['needs_attention'])
        self.assertIsNone(s3_row['attempt_id'])
        self.assertIsNone(s3_row['remaining_seconds'])

    def test_proctor_console_cross_tenant_isolation(self):
        """Cross-tenant access to exam proctor endpoint returns HTTP 404."""
        # Create second foundation and unauthorized user
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Yayasan Lain",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id,
            name="Sekolah B",
            npsn="99999",
            level=School.LEVEL_SMA,
        )
        other_user = User.objects.create(
            foundation_id=other_foundation.id,
            phone_e164="+6281199990001",
            full_name="Guru Yayasan B",
            is_active=True,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=other_foundation.id,
            user=other_user,
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=other_school.id,
        )

        self.client.force_authenticate(user=other_user)
        response = self.client.get(f'/api/v1/academic/exams/{self.exam.id}/proctor/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_proctor_console_permissions(self):
        """Users without grades.read permission receive 403 Forbidden."""
        unauth_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281199990002",
            full_name="Siswa Tanpa Akses",
            is_active=True,
        )
        # Give no role assignments
        self.client.force_authenticate(user=unauth_user)
        response = self.client.get(f'/api/v1/academic/exams/{self.exam.id}/proctor/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_exam_attempt_idempotent_autosave(self):
        """ExamAttemptViewSet.answers supports Idempotency-Key header for network-safe retries."""
        att = start_attempt(self.exam, self.s1)
        self.client.force_authenticate(user=self.teacher_user)

        idempotency_key = f"exam_autosave:{att.id}:{self.q1.id}:v1"
        payload = {'question_id': self.q1.id, 'answer': {'selected': 'A'}}

        # First dispatch
        res1 = self.client.patch(
            f'/api/v1/academic/exam-attempts/{att.id}/answers/',
            payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(res1.status_code, status.HTTP_200_OK, res1.content)

        # Replay with same key and same payload
        res2 = self.client.patch(
            f'/api/v1/academic/exam-attempts/{att.id}/answers/',
            payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.headers.get('X-Cache'), 'HIT-Idempotent')

        # Replay with same key but differing payload -> 409 Conflict
        differing_payload = {'question_id': self.q1.id, 'answer': {'selected': 'B'}}
        res3 = self.client.patch(
            f'/api/v1/academic/exam-attempts/{att.id}/answers/',
            differing_payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(res3.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res3.json().get('error'), 'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD')

    def test_focus_loss_counter_increment_without_auto_punish(self):
        """ACD-024: Focus loss increments counter and does not terminate attempt."""
        att = start_attempt(self.exam, self.s1)
        self.assertEqual(att.focus_loss_count, 0)
        self.assertEqual(att.status, ExamAttemptStatus.IN_PROGRESS)

        self.client.force_authenticate(user=self.teacher_user)
        for i in range(1, 5):
            res = self.client.post(f'/api/v1/academic/exam-attempts/{att.id}/focus-loss/')
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertEqual(res.json()['focus_loss_count'], i)
            self.assertEqual(res.json()['status'], ExamAttemptStatus.IN_PROGRESS)

        att.refresh_from_db()
        self.assertEqual(att.focus_loss_count, 4)
        self.assertEqual(att.status, ExamAttemptStatus.IN_PROGRESS)

    def test_templates_rendering_syntax(self):
        """Ensure exam_lockdown.html and exam_proctor_console.html render cleanly with context."""
        att = start_attempt(self.exam, self.s1)
        questions = [self.q1, self.q2]

        # 1. Test student lockdown template
        lockdown_context = {
            'exam': self.exam,
            'attempt': att,
            'questions': questions,
            'remaining_seconds': 5400,
            'csrf_token': 'dummy-csrf-token',
        }
        lockdown_html = render_to_string('components/exam_lockdown.html', lockdown_context)
        self.assertIn('exam-lockdown-app', lockdown_html)
        self.assertIn('Ujian Akhir Semester Fisika', lockdown_html)
        self.assertIn('Daftar Soal', lockdown_html)
        self.assertIn('Kumpulkan Ujian', lockdown_html)
        self.assertIn('Mode Layar Penuh Diwajibkan', lockdown_html)

        # 2. Test proctor console template
        proctor_data = {
            'summary': {
                'total_students': 3,
                'in_progress': 1,
                'submitted': 1,
                'not_started': 1,
                'flagged_focus_loss': 1,
            },
            'students': [
                {
                    'student_id': self.s1.id,
                    'student_name': 'Ahmad Dahlan',
                    'nis': '2026001',
                    'attempt_id': att.id,
                    'status': 'IN_PROGRESS',
                    'answered_count': 1,
                    'total_questions': 2,
                    'progress_pct': 50,
                    'focus_loss_count': 1,
                    'last_saved_at': timezone.now(),
                    'remaining_seconds': 5400,
                    'needs_attention': False,
                }
            ]
        }
        proctor_context = {
            'exam': self.exam,
            'proctor_data': proctor_data,
            'csrf_token': 'dummy-csrf-token',
        }
        proctor_html = render_to_string('components/exam_proctor_console.html', proctor_context)
        self.assertIn('proctor-console-app', proctor_html)
        self.assertIn('Konsol Pengawas Ujian', proctor_html)
        self.assertIn('Sedang Mengerjakan', proctor_html)
        self.assertIn('Paksa Kumpulkan', proctor_html)

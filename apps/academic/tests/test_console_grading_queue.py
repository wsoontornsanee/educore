"""Web console: Antrean penilaian page (read-only homework grading queue). Spec:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md (PR 3)."""
import datetime
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import (
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus as S,
    Subject,
)
from apps.academic.services import get_homework_grading_queue
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.models import Person, RoleAssignment, Student
from educore.middleware.tenancy import set_current_foundation_id

URL = '/web/academic/grading-queue/'


class GradingQueueConsoleTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Antrean Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.class_subject = self.fx['class_subject']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628133000004", "wali@antrean.test", "Wali Antrean",
            "3471010101022004", student=self.fx['student'],
        )
        self.homework = self._homework('PR Aljabar')
        self.now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher.user)

    def _homework(self, title, class_subject=None):
        return Homework.objects.create(
            foundation_id=self.foundation.id, class_subject=class_subject or self.class_subject,
            title=title, assigned_at=timezone.now() - datetime.timedelta(days=5),
            due_at=timezone.now() - datetime.timedelta(days=1),
        )

    def _student(self, name, nis):
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        return Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person, nis=nis,
            status=Student.STATUS_ACTIVE,
        )

    def _submit(self, student, status, hours_ago, homework=None):
        return HomeworkSubmission.objects.create(
            foundation_id=self.foundation.id, homework=homework or self.homework, student=student,
            submitted_at=self.now - datetime.timedelta(hours=hours_ago), status=status,
        )

    def test_lists_only_ungraded_oldest_first_with_counts(self):
        budi, citra, dewi, eko = (self._student(n, f'X-{i}') for i, n in enumerate(
            ['Budi Santoso', 'Citra Lestari', 'Dewi Anggraini', 'Eko Prasetyo'], start=2))
        self._submit(budi, S.SUBMITTED, hours_ago=2)
        self._submit(citra, S.LATE, hours_ago=10)
        self._submit(dewi, S.GRADED, hours_ago=20)
        self._submit(eko, S.RETURNED, hours_ago=30)
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn('Budi Santoso', body)
        self.assertIn('Citra Lestari', body)
        self.assertNotIn('Dewi Anggraini', body)
        self.assertNotIn('Eko Prasetyo', body)
        self.assertLess(body.index('Citra Lestari'), body.index('Budi Santoso'))  # oldest first
        self.assertContains(res, 'PR Aljabar')
        self.assertContains(res, 'Matematika — X IPA 1')
        self.assertEqual(res.context['total_count'], 2)
        self.assertEqual(res.context['late_count'], 1)

    def test_class_subject_filter_and_malformed_filter(self):
        other_subject = Subject.objects.create(
            foundation_id=self.foundation.id, school=self.school, code='FIS', name='Fisika', credit_hours=3,
        )
        other_cs = ClassSubject.objects.create(
            foundation_id=self.foundation.id, class_group=self.fx['class_group'], subject=other_subject,
            teacher=self.teacher, term=self.fx['term'],
        )
        self._submit(self._student('Budi Santoso', 'X-2'), S.SUBMITTED, 1)
        self._submit(self._student('Citra Lestari', 'X-3'), S.SUBMITTED, 1, homework=self._homework('PR Gaya', other_cs))
        res = self.client.get(URL, {'class_subject': other_cs.id})
        self.assertContains(res, 'Citra Lestari')
        self.assertNotContains(res, 'Budi Santoso')
        res = self.client.get(URL, {'class_subject': 'abc'})
        self.assertContains(res, 'Budi Santoso')
        self.assertContains(res, 'Citra Lestari')

    def test_empty_state(self):
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Antrean kosong')

    def test_soft_deleted_submission_hidden(self):
        sub = self._submit(self._student('Budi Santoso', 'X-2'), S.SUBMITTED, 1)
        HomeworkSubmission.all_tenants.filter(id=sub.id).update(deleted_at=timezone.now())
        self.assertNotContains(self.client.get(URL), 'Budi Santoso')

    def test_row_cap_shows_truncation_note(self):
        for i in range(3):
            self._submit(self._student(f'Siswa {i}', f'X-{i + 2}'), S.SUBMITTED, hours_ago=i + 1)
        with mock.patch('apps.academic.console_views.GRADING_QUEUE_PAGE_SIZE', 2):
            res = self.client.get(URL)
        self.assertEqual(len(res.context['submissions']), 2)
        self.assertContains(res, 'Menampilkan 2 pengumpulan tertua dari 3.')

    def test_query_count_does_not_grow_with_submissions(self):
        self._submit(self._student('Siswa A', 'X-2'), S.SUBMITTED, 1)
        self.client.get(URL)  # warm

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(URL)
            return len(ctx.captured_queries)

        before = count()
        for i in range(5):
            self._submit(self._student(f'Siswa {i}', f'Y-{i}'), S.LATE, 2)
        self.assertEqual(count(), before)

    def test_other_foundation_submissions_never_leak(self):
        other = build_academic_fixture("Yayasan Lain Antrean")
        hw = Homework.all_tenants.create(
            foundation_id=other['foundation'].id, class_subject=other['class_subject'], title='RAHASIA-LAIN',
            assigned_at=timezone.now(), due_at=timezone.now(),
        )
        HomeworkSubmission.all_tenants.create(
            foundation_id=other['foundation'].id, homework=hw, student=other['student'],
            submitted_at=timezone.now(), status=S.SUBMITTED,
        )
        set_current_foundation_id(self.foundation.id)
        self.assertNotContains(self.client.get(URL), 'RAHASIA-LAIN')
        # A cross-tenant class_subject filter yields an empty queue, not the other tenant's rows
        res = self.client.get(URL, {'class_subject': other['class_subject'].id})
        self.assertNotContains(res, 'RAHASIA-LAIN')
        self.assertEqual(res.context['total_count'], 0)

    def test_rejects_guardian_without_staff_profile_and_anonymous(self):
        self.client.force_authenticate(user=self.guardian_user)
        self.assertEqual(self.client.get(URL).status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))


class GradingQueueServiceTests(TestCase):
    def test_service_scopes_by_foundation_and_supports_include_graded(self):
        fx = build_academic_fixture("Yayasan Service Antrean")
        other = build_academic_fixture("Yayasan Service Lain")
        set_current_foundation_id(fx['foundation'].id)
        hw = Homework.objects.create(
            foundation_id=fx['foundation'].id, class_subject=fx['class_subject'], title='PR',
            assigned_at=timezone.now(), due_at=timezone.now(),
        )
        for status in (S.SUBMITTED, S.GRADED):
            person = Person.all_tenants.create(foundation_id=fx['foundation'].id, full_name=f'S {status}')
            student = Student.all_tenants.create(
                foundation_id=fx['foundation'].id, school=fx['school'], person=person,
                nis=f'N-{status}', status=Student.STATUS_ACTIVE,
            )
            HomeworkSubmission.objects.create(
                foundation_id=fx['foundation'].id, homework=hw, student=student,
                submitted_at=timezone.now(), status=status,
            )
        self.assertEqual(get_homework_grading_queue(fx['foundation'].id).count(), 1)
        self.assertEqual(get_homework_grading_queue(fx['foundation'].id, include_graded=True).count(), 2)
        self.assertEqual(get_homework_grading_queue(other['foundation'].id, include_graded=True).count(), 0)

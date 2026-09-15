"""ACD-026 / spec/04 §9 load test: verify an exam is runnable for N concurrent
students without score loss, and that batch auto-submit clears the 60-second
acceptance criterion.

Creates a throwaway foundation/school/class/exam/N students (a 40-question
MCQ exam by default, matching spec/04 §9 criterion #3), then:
  1. Runs the full attempt lifecycle (start -> answer every question ->
     submit) concurrently across a thread pool, one "student" per thread —
     verifies ACD-026's "without score loss": every attempt persists with
     the correct auto_score.
  2. Closes the exam window and times auto_submit_expired_attempts against
     all of them at once — verifies spec/04 §9 criterion #3's 60-second
     bound (see apps.academic.services.auto_submit_expired_attempts and its
     per-minute cron command, which is what actually makes this criterion
     achievable for a student who never revisits the exam URL).

IMPORTANT: SQLite (EDUCORE_USE_SQLITE=1, this repo's local/dev fallback)
serializes every write under one database-level lock and cannot exercise
real concurrent-connection behavior. Run this against MySQL — the actual
production database (spec/01 "MySQL 8 only") — for a meaningful result;
against SQLite it still validates correctness (no score loss), just not
genuine concurrency.

Usage:
    python manage.py load_test_exam --students=300 --concurrency=50
    python manage.py load_test_exam --students=300 --concurrency=50 --cleanup
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.academic.models import (
    AcademicYear,
    ClassGroup,
    ClassSubject,
    Exam,
    ExamAttempt,
    ExamAttemptStatus,
    ExamMode,
    ExamQuestion,
    ExamQuestionType,
    Subject,
    Term,
)
from apps.academic.services import auto_submit_expired_attempts, save_answer, start_attempt, submit_attempt
from apps.identity.models import Foundation, Person, School, Staff, Student, User
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


class Command(BaseCommand):
    help = "ACD-026 load test: N concurrent students taking an MCQ exam, plus a batch auto-submit timing check."

    def add_arguments(self, parser):
        parser.add_argument('--students', type=int, default=300)
        parser.add_argument('--questions', type=int, default=40)
        parser.add_argument('--concurrency', type=int, default=50)
        parser.add_argument('--cleanup', action='store_true', help="Delete the throwaway fixture data after the test.")

    def handle(self, *args, **options):
        num_students = options['students']
        num_questions = options['questions']
        concurrency = options['concurrency']

        self.stdout.write(f"Setting up fixture: {num_students} students, two {num_questions}-question MCQ exams...")
        foundation, exam_submit, exam_straggler, students, questions_submit, questions_straggler = \
            self._setup_fixture(num_students, num_questions)

        # Phase 1 (exam_submit): every student answers and explicitly submits,
        # concurrently — the normal path. Verifies ACD-026's "without score
        # loss" under concurrent live traffic.
        self.stdout.write(f"Phase 1: {num_students} concurrent students answer + submit (concurrency={concurrency})...")
        start = time.monotonic()
        results = self._run_concurrent_attempts(foundation.id, exam_submit, students, questions_submit, concurrency, do_submit=True)
        elapsed = time.monotonic() - start
        self._report_concurrent_phase(foundation.id, exam_submit, num_students, num_questions, results, elapsed, "Phase 1 (explicit submit)")

        # Phase 2 (exam_straggler): every student answers but NEVER submits —
        # simulates students who ran out of time and never revisited the exam
        # URL. Verifies spec/04 §9 criterion #3: the batch auto-submit sweep
        # (apps.academic.services.auto_submit_expired_attempts, run every
        # minute by cron) clears all of them within 60 seconds of window
        # close, which is the ONLY mechanism that could satisfy this
        # criterion for a student who never comes back.
        self.stdout.write(f"Phase 2: {num_students} concurrent students answer but never submit (concurrency={concurrency})...")
        start2 = time.monotonic()
        results2 = self._run_concurrent_attempts(foundation.id, exam_straggler, students, questions_straggler, concurrency, do_submit=False)
        elapsed2 = time.monotonic() - start2
        stragglers_ok = sum(1 for r in results2 if r['ok'])
        self.stdout.write(f"Phase 2 setup: {stragglers_ok}/{num_students} attempts left IN_PROGRESS in {elapsed2:.2f}s.")

        self.stdout.write("Timing the batch auto-submit path against Phase 2's stragglers (60s bound)...")
        with tenant_context(foundation.id):
            Exam.all_tenants.filter(id=exam_straggler.id).update(window_end=timezone.now() - timedelta(seconds=1))
            batch_start = time.monotonic()
            batch_result = auto_submit_expired_attempts(foundation_id=foundation.id)
            batch_elapsed = time.monotonic() - batch_start

        within_bound = batch_elapsed <= 60
        style = self.style.SUCCESS if within_bound and batch_result['submitted'] == stragglers_ok else self.style.ERROR
        self.stdout.write(style(
            f"Batch auto-submit: {batch_result['submitted']}/{stragglers_ok} straggler attempt(s) submitted "
            f"in {batch_elapsed:.2f}s ({'within' if within_bound else 'EXCEEDS'} the 60-second bound)."
        ))

        if options['cleanup']:
            self.stdout.write(f"Cleaning up fixture (foundation #{foundation.id})...")
            self._cleanup_fixture(foundation)
            self.stdout.write(self.style.SUCCESS("Cleanup complete."))
        else:
            self.stdout.write(self.style.WARNING(
                f"Fixture left in place (foundation #{foundation.id}) — pass --cleanup to remove it."
            ))

    def _setup_fixture(self, num_students, num_questions):
        foundation = Foundation.objects.create(
            legal_name="Yayasan Load Test", brand_name="Yayasan Load Test",
            npwp="00.000.000.0-000.000", address="Load Test",
        )
        set_current_foundation_id(foundation.id)

        school = School.all_tenants.create(
            foundation_id=foundation.id, name="SMP Load Test",
            npsn=f"90{foundation.id:06d}", level=School.LEVEL_SMP,
        )
        academic_year = AcademicYear.objects.create(
            foundation_id=foundation.id, school=school, name="2026/2027",
            start_date=timezone.now().date(), end_date=timezone.now().date() + timedelta(days=300),
        )
        class_group = ClassGroup.objects.create(
            foundation_id=foundation.id, school=school, academic_year=academic_year,
            grade_level=7, name="VII Load Test", capacity=num_students,
        )
        subject = Subject.objects.create(foundation_id=foundation.id, school=school, code="LOADTEST", name="Load Test Subject")
        term = Term.objects.create(
            foundation_id=foundation.id, academic_year=academic_year, name="Semester 1", term_no=1,
            start_date=academic_year.start_date, end_date=academic_year.end_date,
        )
        # Every unique-constrained field is derived from foundation.id (a real
        # auto-increment PK, never reused even if a prior run's --cleanup was
        # skipped or failed), so repeated runs never collide regardless of
        # whether earlier fixtures were ever cleaned up.
        teacher_person = Person.all_tenants.create(
            foundation_id=foundation.id, nik=f"{foundation.id:016d}", full_name="Load Test Teacher",
        )
        teacher_user = User.objects.create(
            foundation_id=foundation.id, phone_e164=f"+62900{foundation.id:08d}",
            email=f"loadtest.teacher.{foundation.id}@example.id", full_name="Load Test Teacher",
        )
        teacher = Staff.all_tenants.create(
            foundation_id=foundation.id, person=teacher_person, user=teacher_user, school=school,
            employment_type=Staff.TYPE_PERMANENT, join_date=timezone.now().date(),
        )
        class_subject = ClassSubject.objects.create(
            foundation_id=foundation.id, class_group=class_group, subject=subject, teacher=teacher, term=term,
        )

        def make_exam(title_suffix):
            exam = Exam.objects.create(
                foundation_id=foundation.id, class_subject=class_subject, title=f"Load Test Exam {title_suffix}",
                mode=ExamMode.ONLINE, window_start=timezone.now() - timedelta(minutes=5),
                window_end=timezone.now() + timedelta(hours=2), duration_min=120, published=True,
            )
            questions = [
                ExamQuestion.objects.create(
                    foundation_id=foundation.id, exam=exam, seq=i, type=ExamQuestionType.MCQ,
                    body=f"Question {i}", options=[{'key': 'A', 'text': 'A'}, {'key': 'B', 'text': 'B'}],
                    points=Decimal('1.00'), answer_key={'correct': 'A'},
                )
                for i in range(1, num_questions + 1)
            ]
            return exam, questions

        # Two separate exams (same class/students) so Phase 1's explicit
        # submissions can't leave Phase 2 with nothing IN_PROGRESS to sweep —
        # a student may only have one attempt per exam (unique constraint).
        exam_submit, questions_submit = make_exam("(Submit)")
        exam_straggler, questions_straggler = make_exam("(Straggler)")

        students = []
        for i in range(num_students):
            person = Person.all_tenants.create(foundation_id=foundation.id, nik=f"0000000001{i:06d}", full_name=f"Load Test Student {i}")
            student = Student.all_tenants.create(
                foundation_id=foundation.id, school=school, person=person,
                nisn=f"1{i:09d}", nis=f"LOAD-{i:04d}", status=Student.STATUS_ACTIVE,
            )
            students.append(student)

        return foundation, exam_submit, exam_straggler, students, questions_submit, questions_straggler

    def _cleanup_fixture(self, foundation):
        """foundation_id is a plain field on TenantModel (ARC-001), not a real
        ForeignKey — Foundation.delete() does NOT cascade to any of this
        fixture's rows. Every model must be torn down explicitly, deepest
        dependents first, or the PROTECT constraints on the shallower ones
        (School, Person, User, ...) raise instead of deleting.
        """
        from apps.academic.models import ExamAnswer

        with tenant_context(foundation.id):
            ExamAnswer.all_tenants.filter(foundation_id=foundation.id).delete()
            ExamAttempt.all_tenants.filter(foundation_id=foundation.id).delete()
            ExamQuestion.all_tenants.filter(foundation_id=foundation.id).delete()
            Exam.all_tenants.filter(foundation_id=foundation.id).delete()
            ClassSubject.all_tenants.filter(foundation_id=foundation.id).delete()
            ClassGroup.all_tenants.filter(foundation_id=foundation.id).delete()
            Student.all_tenants.filter(foundation_id=foundation.id).delete()
            Staff.all_tenants.filter(foundation_id=foundation.id).delete()
            Term.all_tenants.filter(foundation_id=foundation.id).delete()
            AcademicYear.all_tenants.filter(foundation_id=foundation.id).delete()
            Subject.all_tenants.filter(foundation_id=foundation.id).delete()
            School.all_tenants.filter(foundation_id=foundation.id).delete()
            User.objects.filter(foundation_id=foundation.id).delete()
            Person.all_tenants.filter(foundation_id=foundation.id).delete()
        foundation.delete()

    def _run_concurrent_attempts(self, foundation_id, exam, students, questions, concurrency, do_submit):
        def run_one(student):
            set_current_foundation_id(foundation_id)
            try:
                attempt = start_attempt(exam, student)
                for q in questions:
                    save_answer(attempt, q, {'selected': 'A'})
                if do_submit:
                    submit_attempt(attempt)
                return {'student_id': student.id, 'ok': True, 'error': None}
            except Exception as exc:
                return {'student_id': student.id, 'ok': False, 'error': str(exc)}
            finally:
                connection.close()  # each thread gets its own DB connection

        results = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(run_one, s) for s in students]
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def _report_concurrent_phase(self, foundation_id, exam, num_students, num_questions, results, elapsed, label):
        failed = [r for r in results if not r['ok']]
        expected_score = Decimal(num_questions) * Decimal('1.00')
        with tenant_context(foundation_id):
            attempts = list(ExamAttempt.all_tenants.filter(exam=exam, status=ExamAttemptStatus.SUBMITTED))
        wrong_score = [a for a in attempts if a.auto_score != expected_score]

        if not failed and not wrong_score and len(attempts) == num_students:
            self.stdout.write(self.style.SUCCESS(
                f"{label} OK: {len(attempts)}/{num_students} attempts persisted correctly "
                f"in {elapsed:.2f}s ({num_students / elapsed:.1f} attempts/sec)."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"{label}: {len(attempts)}/{num_students} attempts persisted, "
                f"{len(failed)} request(s) failed, {len(wrong_score)} with a WRONG SCORE (score loss) "
                f"in {elapsed:.2f}s."
            ))
        for r in failed[:10]:
            self.stdout.write(self.style.ERROR(f"  student #{r['student_id']}: {r['error']}"))

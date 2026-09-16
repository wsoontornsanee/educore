"""Tests for Homework Grading Queue Integration (TCH-014, ACD-028, spec/09, spec/17)."""
import datetime
from decimal import Decimal
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academic.models import (
    AcademicYear,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    School,
    Subject,
    Term,
)
from apps.academic.services import (
    assign_homework,
    grade_homework_submission,
    return_homework_submission,
    submit_homework,
)
from apps.identity.models import Foundation, Person, Staff, Student, User
from apps.identity.rbac import assign_role, ROLE_TEACHER, SCOPE_SCHOOL
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class HomeworkGradingQueueTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan EduCore Cendekia",
            brand_name="EduCore Cendekia",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Luar",
            brand_name="Luar",
        )

        with tenant_context(self.foundation.id):
            self.school = School.objects.create(
                foundation_id=self.foundation.id,
                name="SMA EduCore",
                npsn="87654321",
                level=School.LEVEL_SMA,
            )

        self.teacher_user = User.all_tenants.create_user(
            phone_e164="+6281100000001",
            foundation_id=self.foundation.id,
            full_name="Ibu Siti Rahayu",
        )
        assign_role(
            user=self.teacher_user,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        self.other_user = User.all_tenants.create_user(
            phone_e164="+6281100000002",
            foundation_id=self.other_foundation.id,
            full_name="Pak Ahmad Luar",
        )

        with tenant_context(self.foundation.id):
            self.teacher_person = Person.objects.create(
                foundation_id=self.foundation.id,
                nik="3201010101010001",
                full_name="Ibu Siti Rahayu",
                gender=Person.GENDER_FEMALE,
            )
            self.staff = Staff.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                user=self.teacher_user,
                person=self.teacher_person,
                nip="198501012010012001",
                employment_type=Staff.TYPE_PERMANENT,
                join_date=timezone.now().date(),
                status=Staff.STATUS_ACTIVE,
            )

            self.academic_year = AcademicYear.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="2026/2027",
                start_date=datetime.date(2026, 7, 1),
                end_date=datetime.date(2027, 6, 30),
                is_active=True,
            )
            self.term = Term.objects.create(
                foundation_id=self.foundation.id,
                academic_year=self.academic_year,
                name="Ganjil",
                term_no=1,
                start_date=datetime.date(2026, 7, 1),
                end_date=datetime.date(2026, 12, 31),
                is_active=True,
            )
            self.subject = Subject.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="Matematika",
                code="MAT-10",
            )
            self.class_group = ClassGroup.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="10-A",
                grade_level=10,
                academic_year=self.academic_year,
            )
            self.class_subject = ClassSubject.objects.create(
                foundation_id=self.foundation.id,
                class_group=self.class_group,
                subject=self.subject,
                teacher=self.staff,
                term=self.term,
            )

            # Two students
            self.p1 = Person.objects.create(
                foundation_id=self.foundation.id,
                nik="3201010101010011",
                full_name="Ahmad Santoso",
                gender=Person.GENDER_MALE,
            )
            self.student1 = Student.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                person=self.p1,
                nis="2026001",
                nisn="0012345671",
                status=Student.STATUS_ACTIVE,
            )
            ClassEnrollment.objects.create(
                foundation_id=self.foundation.id,
                class_group=self.class_group,
                student=self.student1,
                enrolled_at=timezone.now().date(),
                is_active=True,
            )

            self.p2 = Person.objects.create(
                foundation_id=self.foundation.id,
                nik="3201010101010012",
                full_name="Budi Pratama",
                gender=Person.GENDER_MALE,
            )
            self.student2 = Student.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                person=self.p2,
                nis="2026002",
                nisn="0012345672",
                status=Student.STATUS_ACTIVE,
            )
            ClassEnrollment.objects.create(
                foundation_id=self.foundation.id,
                class_group=self.class_group,
                student=self.student2,
                enrolled_at=timezone.now().date(),
                is_active=True,
            )

            # Homework 1: due tomorrow
            now = timezone.now()
            self.hw1 = assign_homework(
                class_subject=self.class_subject,
                title="Tugas 1: Persamaan Linier",
                instructions="Selesaikan soal 1-5",
                assigned_at=now - datetime.timedelta(days=2),
                due_at=now + datetime.timedelta(days=1),
            )
            # Homework 2: due yesterday
            self.hw2 = assign_homework(
                class_subject=self.class_subject,
                title="Tugas 2: Pertidaksamaan",
                instructions="Selesaikan soal 6-10",
                assigned_at=now - datetime.timedelta(days=3),
                due_at=now - datetime.timedelta(days=1),
            )

            # Submissions
            # student1 on hw1 (on-time, submitted 2 hours ago)
            self.sub1 = submit_homework(
                homework=self.hw1,
                student=self.student1,
                text="Jawaban Tugas 1 Ahmad",
                files=[{"key": "uploads/jawaban1.pdf", "filename": "jawaban1.pdf", "size": 1024, "content_type": "application/pdf"}],
            )
            # student2 on hw2 (late, submitted 1 hour ago)
            self.sub2 = submit_homework(
                homework=self.hw2,
                student=self.student2,
                text="Jawaban Tugas 2 Budi terlambat",
            )

    def test_cross_homework_grading_queue_returns_pending_submissions(self):
        """GET /api/v1/academic/homework/grading-queue/?class_subject_id= returns un-graded submissions."""
        self.client.force_authenticate(user=self.teacher_user)
        url = f"/api/v1/academic/homework/grading-queue/?class_subject_id={self.class_subject.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(len(data), 2)

        # Check fields
        item1 = next(item for item in data if item['id'] == self.sub1.id)
        self.assertEqual(item1['student_nis'], "2026001")
        self.assertEqual(item1['student_name'], "Ahmad Santoso")
        self.assertEqual(item1['homework_title'], "Tugas 1: Persamaan Linier")
        self.assertEqual(item1['status'], HomeworkSubmissionStatus.SUBMITTED)
        self.assertEqual(item1['text'], "Jawaban Tugas 1 Ahmad")
        self.assertEqual(len(item1['files']), 1)

        item2 = next(item for item in data if item['id'] == self.sub2.id)
        self.assertEqual(item2['student_nis'], "2026002")
        self.assertEqual(item2['status'], HomeworkSubmissionStatus.LATE)

    def test_single_homework_detail_grading_queue_action(self):
        """GET /api/v1/academic/homework/{id}/grading-queue/ returns queue scoped to that homework."""
        self.client.force_authenticate(user=self.teacher_user)
        url = f"/api/v1/academic/homework/{self.hw1.id}/grading-queue/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['id'], self.sub1.id)

    def test_queue_excludes_graded_by_default_and_includes_on_flag(self):
        """Graded submissions are excluded by default, included when include_graded=true."""
        with tenant_context(self.foundation.id):
            grade_homework_submission(self.sub1, score=Decimal("85.00"), feedback="Bagus", actor=self.teacher_user)

        self.client.force_authenticate(user=self.teacher_user)
        url = f"/api/v1/academic/homework/grading-queue/?class_subject_id={self.class_subject.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # sub1 is GRADED, so only sub2 should be returned
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['id'], self.sub2.id)

        # With include_graded=true
        resp_all = self.client.get(f"{url}&include_graded=true")
        self.assertEqual(resp_all.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp_all.data), 2)

    def test_queue_fifo_ordering(self):
        """Queue orders submissions by submitted_at ascending so oldest submission is first."""
        with tenant_context(self.foundation.id):
            now = timezone.now()
            # Set sub2 earlier than sub1
            HomeworkSubmission.objects.filter(id=self.sub2.id).update(submitted_at=now - datetime.timedelta(hours=5))
            HomeworkSubmission.objects.filter(id=self.sub1.id).update(submitted_at=now - datetime.timedelta(hours=1))

        self.client.force_authenticate(user=self.teacher_user)
        url = f"/api/v1/academic/homework/grading-queue/?class_subject_id={self.class_subject.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data[0]['id'], self.sub2.id)
        self.assertEqual(resp.data[1]['id'], self.sub1.id)

    def test_cross_tenant_isolation(self):
        """Users from a sibling foundation receive 404 or empty results."""
        self.client.force_authenticate(user=self.other_user)
        # Detail route returns 404
        url_detail = f"/api/v1/academic/homework/{self.hw1.id}/grading-queue/"
        resp_detail = self.client.get(url_detail)
        self.assertIn(resp_detail.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

        # List route returns empty
        url_list = f"/api/v1/academic/homework/grading-queue/?class_subject_id={self.class_subject.id}"
        resp_list = self.client.get(url_list)
        if resp_list.status_code == status.HTTP_200_OK:
            self.assertEqual(len(resp_list.data), 0)
        else:
            self.assertEqual(resp_list.status_code, status.HTTP_403_FORBIDDEN)

    def test_grading_queue_template_rendering(self):
        """Component template renders populated and empty queue states."""
        # 1. Populated state
        queue_items = [
            {
                'id': str(self.sub1.id),
                'student_nis': '2026001',
                'student_name': 'Ahmad Santoso',
                'homework_title': 'Tugas 1',
                'class_subject_name': 'Matematika - 10-A',
                'status': 'SUBMITTED',
                'submitted_at': timezone.now(),
                'text': 'Jawaban Ahmad',
                'files': [{'filename': 'dokumen.pdf', 'size': 1024, 'key': '/media/dokumen.pdf'}],
                'score': None,
                'feedback': '',
            }
        ]
        html = render_to_string("components/grading_queue.html", {
            'queue': queue_items,
            'homework': self.hw1,
            'class_subject': self.class_subject,
        })
        self.assertIn("ANTREAN PENILAIAN", html)
        self.assertIn("Ahmad Santoso", html)
        self.assertIn("NIS: 2026001", html)
        self.assertIn("TERKUMPUL", html)
        self.assertIn("Jawaban Ahmad", html)
        self.assertIn("dokumen.pdf", html)
        self.assertIn("Ctrl+Enter", html)
        self.assertIn("Alt+R", html)

        # 2. Empty state
        html_empty = render_to_string("components/grading_queue.html", {
            'queue': [],
            'homework': self.hw1,
        })
        self.assertIn("Semua Pengajuan Telah Dinilai", html_empty)
        self.assertIn("0 Antrean", html_empty)

    def test_grade_and_return_api_integration(self):
        """Teacher can submit grade and return submission via API."""
        self.client.force_authenticate(user=self.teacher_user)

        # 1. Grade submission
        grade_url = f"/api/v1/academic/homework-submissions/{self.sub1.id}/grade/"
        resp_grade = self.client.post(grade_url, {'score': 92.5, 'feedback': 'Sangat baik'})
        self.assertEqual(resp_grade.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_grade.data['status'], HomeworkSubmissionStatus.GRADED)
        self.assertEqual(Decimal(resp_grade.data['score']), Decimal("92.50"))

        # 2. Return submission for revision (ACD-028)
        return_url = f"/api/v1/academic/homework-submissions/{self.sub2.id}/return/"
        resp_return = self.client.post(return_url, {'feedback': 'Perbaiki nomor 8'})
        self.assertEqual(resp_return.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_return.data['status'], HomeworkSubmissionStatus.RETURNED)
        self.assertEqual(resp_return.data['feedback'], 'Perbaiki nomor 8')

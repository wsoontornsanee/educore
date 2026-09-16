"""Tests for Homework Completion Bar and Reminder Delivery Receipts (ACD-030, spec/04 §7, spec/17)."""
from datetime import timedelta
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
    Subject,
    Term,
)
from apps.academic.services import (
    get_homework_completion,
    get_homework_remind_status,
    remind_unsubmitted,
)
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Staff, Student, User
from apps.identity.rbac import assign_role, ROLE_TEACHER, SCOPE_SCHOOL
from apps.notifications.models import (
    ChannelType,
    DeliveryStatus,
    NotificationCategory,
    NotificationDelivery,
    NotificationIntent,
    NotificationTemplate,
)
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class HomeworkCompletionTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan EduCore Cendekia",
            brand_name="EduCore Cendekia",
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Lain",
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
            full_name="Guru Matematika",
        )
        assign_role(
            user=self.teacher_user,
            role=ROLE_TEACHER,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        with tenant_context(self.foundation.id):
            self.teacher_person = Person.objects.create(
                foundation_id=self.foundation.id,
                full_name="Guru Matematika",
            )
            self.teacher = Staff.objects.create(
                foundation_id=self.foundation.id,
                person=self.teacher_person,
                user=self.teacher_user,
                school=self.school,
                employment_type=Staff.TYPE_PERMANENT,
                join_date=timezone.now().date(),
                status=Staff.STATUS_ACTIVE,
            )
            self.academic_year = AcademicYear.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="2026/2027",
                start_date=timezone.now().date(),
                end_date=timezone.now().date() + timedelta(days=365),
            )
            self.term = Term.objects.create(
                foundation_id=self.foundation.id,
                academic_year=self.academic_year,
                name="Ganjil",
                term_no=1,
                start_date=timezone.now().date(),
                end_date=timezone.now().date() + timedelta(days=180),
            )
            self.class_group = ClassGroup.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="X-IPA-1",
                grade_level=10,
                academic_year=self.academic_year,
            )
            self.subject = Subject.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                name="Matematika",
                code="MAT10",
                level=School.LEVEL_SMA,
            )
            self.class_subject = ClassSubject.objects.create(
                foundation_id=self.foundation.id,
                class_group=self.class_group,
                subject=self.subject,
                teacher=self.teacher,
                term=self.term,
            )

            # Create 5 enrolled students
            self.students = []
            for i in range(1, 6):
                person = Person.objects.create(
                    foundation_id=self.foundation.id,
                    full_name=f"Siswa {i}",
                )
                student = Student.objects.create(
                    foundation_id=self.foundation.id,
                    school=self.school,
                    person=person,
                    nis=f"NIS00{i}",
                )
                ClassEnrollment.objects.create(
                    foundation_id=self.foundation.id,
                    class_group=self.class_group,
                    student=student,
                    enrolled_at=timezone.now().date(),
                    is_active=True,
                )
                self.students.append(student)

            self.now = timezone.now()
            self.due_at = self.now + timedelta(days=2)
            self.homework = Homework.objects.create(
                foundation_id=self.foundation.id,
                class_subject=self.class_subject,
                title="Latihan Turunan Fungsi",
                instructions="Kerjakan soal nomor 1-10",
                assigned_at=self.now - timedelta(days=1),
                due_at=self.due_at,
            )

    def tearDown(self):
        clear_current_foundation_id()

    def test_get_homework_completion_4_segments_exact_math(self):
        with tenant_context(self.foundation.id):
            # Student 0: on-time submitted & graded
            HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id,
                homework=self.homework,
                student=self.students[0],
                submitted_at=self.now,
                status=HomeworkSubmissionStatus.GRADED,
                score=Decimal('85.00'),
            )
            # Student 1: late submitted & graded (counts as graded in priority)
            HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id,
                homework=self.homework,
                student=self.students[1],
                submitted_at=self.due_at + timedelta(hours=3),
                status=HomeworkSubmissionStatus.GRADED,
                score=Decimal('70.00'),
            )
            # Student 2: on-time submitted, pending grading
            HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id,
                homework=self.homework,
                student=self.students[2],
                submitted_at=self.now,
                status=HomeworkSubmissionStatus.SUBMITTED,
            )
            # Student 3: late submitted, pending grading
            HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id,
                homework=self.homework,
                student=self.students[3],
                submitted_at=self.due_at + timedelta(hours=1),
                status=HomeworkSubmissionStatus.LATE,
            )
            # Student 4: not submitted (missing)

            result = get_homework_completion(self.homework)

            self.assertEqual(result['total'], 5)
            self.assertEqual(result['graded'], 2)
            self.assertEqual(result['submitted'], 1)
            self.assertEqual(result['late'], 1)
            self.assertEqual(result['missing'], 1)
            self.assertEqual(result['not_started'], 1)

            # Mathematical invariant: sum of segments must equal total
            sum_segments = (
                result['graded'] + result['submitted'] + result['late'] + result['missing']
            )
            self.assertEqual(sum_segments, result['total'])

            # Percentages
            self.assertEqual(result['percentages']['graded'], 40.0)
            self.assertEqual(result['percentages']['submitted'], 20.0)
            self.assertEqual(result['percentages']['late'], 20.0)
            self.assertEqual(result['percentages']['missing'], 20.0)

    def test_completion_api_endpoint(self):
        self.client.force_authenticate(user=self.teacher_user)
        with tenant_context(self.foundation.id):
            HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id,
                homework=self.homework,
                student=self.students[0],
                submitted_at=self.now,
                status=HomeworkSubmissionStatus.SUBMITTED,
            )
            url = f"/api/v1/academic/homework/{self.homework.id}/completion/"
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['total'], 5)
            self.assertEqual(resp.data['submitted'], 1)
            self.assertEqual(resp.data['missing'], 4)
            self.assertIn('percentages', resp.data)

    def test_get_homework_remind_status_empty(self):
        with tenant_context(self.foundation.id):
            status_data = get_homework_remind_status(self.homework)
            self.assertEqual(status_data['homework_id'], self.homework.id)
            self.assertEqual(status_data['recipient_count'], 0)
            self.assertEqual(status_data['recipients'], [])

    def test_get_homework_remind_status_with_deliveries(self):
        with tenant_context(self.foundation.id):
            # Setup notification template so remind_unsubmitted works cleanly
            NotificationTemplate.objects.create(
                foundation_id=self.foundation.id,
                school=self.school,
                key='academic.homework.reminder',
                channel=ChannelType.WHATSAPP,
                body='Pengingat tugas: {title}',
                variables=['title', 'subject', 'due_at'],
            )

            # Setup guardian for student 4
            guardian_person = Person.objects.create(
                foundation_id=self.foundation.id,
                full_name="Bapak Siswa 4",
            )
            guardian_user = User.all_tenants.create_user(
                phone_e164="+6281299990004",
                foundation_id=self.foundation.id,
                full_name="Bapak Siswa 4",
            )
            guardian = Guardian.objects.create(
                foundation_id=self.foundation.id,
                person=guardian_person,
                user=guardian_user,
            )
            GuardianLink.objects.create(
                foundation_id=self.foundation.id,
                student=self.students[4],
                guardian=guardian,
                relation=GuardianLink.RELATION_FATHER,
                is_primary=True,
            )

            # Fire reminder
            remind_unsubmitted(self.homework)

            # Find created intent and attach a simulated delivery record
            intent = NotificationIntent.objects.filter(
                foundation_id=self.foundation.id,
                dedupe_key__startswith=f"homework_reminder:{self.homework.id}:",
            ).first()
            self.assertIsNotNone(intent)

            NotificationDelivery.objects.create(
                foundation_id=self.foundation.id,
                intent=intent,
                channel=ChannelType.WHATSAPP,
                provider='mock',
                status=DeliveryStatus.DELIVERED,
                recipient_target="+6281299990004",
                delivered_at=timezone.now(),
            )

            # Test service function
            status_data = get_homework_remind_status(self.homework)
            self.assertEqual(status_data['recipient_count'], 1)
            recipient = status_data['recipients'][0]
            self.assertEqual(recipient['recipient_name'], "Bapak Siswa 4")
            self.assertEqual(recipient['recipient_phone'], "+6281299990004")
            self.assertEqual(len(recipient['deliveries']), 1)
            self.assertEqual(recipient['deliveries'][0]['channel'], ChannelType.WHATSAPP)
            self.assertEqual(recipient['deliveries'][0]['status'], DeliveryStatus.DELIVERED)

    def test_remind_status_api_endpoint(self):
        self.client.force_authenticate(user=self.teacher_user)
        with tenant_context(self.foundation.id):
            url = f"/api/v1/academic/homework/{self.homework.id}/remind-status/"
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['homework_id'], self.homework.id)
            self.assertIn('recipients', resp.data)

    def test_cross_tenant_isolation_returns_404(self):
        with tenant_context(self.other_foundation.id):
            other_school = School.objects.create(
                foundation_id=self.other_foundation.id,
                name="Sekolah Yayasan Lain",
                npsn="99998888",
                level=School.LEVEL_SMA,
            )
            other_user = User.all_tenants.create_user(
                phone_e164="+6281199999999",
                foundation_id=self.other_foundation.id,
                full_name="Guru Yayasan Lain",
            )
            assign_role(
                user=other_user,
                role=ROLE_TEACHER,
                scope_type=SCOPE_SCHOOL,
                scope_id=other_school.id,
                foundation_id=self.other_foundation.id,
            )

        self.client.force_authenticate(user=other_user)
        with tenant_context(self.other_foundation.id):
            # Attempt to access homework belonging to self.foundation
            completion_url = f"/api/v1/academic/homework/{self.homework.id}/completion/"
            resp = self.client.get(completion_url)
            self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

            remind_status_url = f"/api/v1/academic/homework/{self.homework.id}/remind-status/"
            resp2 = self.client.get(remind_status_url)
            self.assertEqual(resp2.status_code, status.HTTP_404_NOT_FOUND)

    def test_template_component_rendering(self):
        completion_data = {
            'total': 25,
            'graded': 10,
            'submitted': 8,
            'late': 2,
            'missing': 5,
            'percentages': {
                'graded': 40.0,
                'submitted': 32.0,
                'late': 8.0,
                'missing': 20.0,
            },
        }
        context = {
            'homework': self.homework,
            'completion': completion_data,
        }
        rendered = render_to_string('components/homework_completion_bar.html', context)
        self.assertIn('Latihan Turunan Fungsi', rendered)
        self.assertIn('bar-segment-graded', rendered)
        self.assertIn('width: 40.0%', rendered)
        self.assertIn('bar-segment-submitted', rendered)
        self.assertIn('width: 32.0%', rendered)
        self.assertIn('bar-segment-late', rendered)
        self.assertIn('width: 8.0%', rendered)
        self.assertIn('bar-segment-missing', rendered)
        self.assertIn('Dinilai', rendered)
        self.assertIn('Terkumpul', rendered)
        self.assertIn('Terlambat', rendered)
        self.assertIn('Belum Mengumpulkan', rendered)
        self.assertIn('Ingatkan Siswa Belum Mengumpulkan (5)', rendered)

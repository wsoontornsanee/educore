import datetime
from decimal import Decimal
from unittest import mock
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import (
    Assessment,
    AssessmentScore,
    AssessmentType,
    ClassEnrollment,
    DayOfWeek,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    ReportCard,
    ReportCardStatus,
    SubstitutionStatus,
    TimetableSlot,
    TimetableSubstitution,
)
from apps.academic.services import approve_report_card, generate_report_cards, publish_report_card, set_arrears_gate
from apps.academic.tests.base import build_academic_fixture
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import set_current_foundation_id


class ParentAcademicViewsTestCase(TestCase):
    def setUp(self):
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_gcs_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/rapor.pdf'
        )

        self.fx = build_academic_fixture("Yayasan Mandiri Parent Academic")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        self.class_group = self.fx['class_group']
        self.class_subject = self.fx['class_subject']
        self.term = self.fx['term']

        set_current_foundation_id(self.foundation.id)

        # Enroll student in class group
        self.enrollment = ClassEnrollment.objects.create(
            foundation_id=self.foundation.id,
            student=self.student,
            class_group=self.class_group,
            enrolled_at=datetime.date(2026, 7, 1),
            is_active=True,
        )

        # Create parent user and guardian link
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101019999",
            full_name="Bapak Budi Wijaya",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281234567890",
            email="wali.budi@cendekia.sch.id",
            full_name="Bapak Budi Wijaya",
        )
        assign_role(
            user=self.parent_user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.parent_user,
            person=self.parent_person,
        )
        self.guardian_link = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
        )

        # Another unlinked parent
        self.unlinked_parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281298765432",
            email="unlinked@cendekia.sch.id",
            full_name="Bapak Lain",
        )
        assign_role(
            user=self.unlinked_parent_user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        self.client = APIClient()

    def test_student_grades_published_only_filter_par010(self):
        """PAR-010: Grades MUST show only published assessments."""
        # 1 Published assessment
        a_pub = Assessment.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.class_subject,
            type=AssessmentType.SUMMATIVE,
            title="Ulangan Harian 1",
            max_score=Decimal('100.00'),
            weight=Decimal('50.00'),
            published=True,
        )
        AssessmentScore.objects.create(
            foundation_id=self.foundation.id,
            assessment=a_pub,
            student=self.student,
            score=Decimal('88.50'),
            descriptor="Baik",
            feedback="Tingkatkan pemahaman aljabar",
        )

        # 1 Draft / Unpublished assessment
        a_draft = Assessment.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.class_subject,
            type=AssessmentType.SUMMATIVE,
            title="Ulangan Rahasia (Draft)",
            max_score=Decimal('100.00'),
            weight=Decimal('50.00'),
            published=False,
        )
        AssessmentScore.objects.create(
            foundation_id=self.foundation.id,
            assessment=a_draft,
            student=self.student,
            score=Decimal('95.00'),
        )

        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/grades/')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['student_id'], self.student.id)
        self.assertEqual(len(data['subjects']), 1)

        subj = data['subjects'][0]
        self.assertEqual(subj['subject_code'], "MTK")
        self.assertEqual(len(subj['assessments']), 1, "Unpublished assessment must not be included")
        self.assertEqual(subj['assessments'][0]['title'], "Ulangan Harian 1")
        self.assertEqual(subj['assessments'][0]['score'], "88.50")
        self.assertEqual(subj['assessments'][0]['descriptor'], "Baik")
        self.assertEqual(subj['assessments'][0]['feedback'], "Tingkatkan pemahaman aljabar")

    def test_student_grades_unlinked_guardian_returns_404(self):
        """IAM-014: Unlinked parent cannot access another student's grades."""
        self.client.force_authenticate(user=self.unlinked_parent_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/grades/')
        self.assertEqual(res.status_code, 404)

    def test_student_homework_view(self):
        """Parent views assigned homework with student's submission status."""
        hw1 = Homework.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.class_subject,
            title="PR Matematika Bab 2",
            instructions="Kerjakan soal no 1-10",
            assigned_at=timezone.now(),
            due_at=timezone.now() + datetime.timedelta(days=2),
        )
        HomeworkSubmission.objects.create(
            foundation_id=self.foundation.id,
            homework=hw1,
            student=self.student,
            status=HomeworkSubmissionStatus.SUBMITTED,
            submitted_at=timezone.now(),
            files=[{'filename': 'jawaban.pdf', 'size': 1024, 'key': 'sub/1.pdf', 'content_type': 'application/pdf'}],
        )

        hw2 = Homework.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.class_subject,
            title="PR Matematika Bab 3",
            instructions="Halaman 45",
            assigned_at=timezone.now(),
            due_at=timezone.now() + datetime.timedelta(days=5),
        )

        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/homework/')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(len(data['results']), 2)

        hw_map = {item['id']: item for item in data['results']}
        self.assertEqual(hw_map[hw1.id]['submission_status'], HomeworkSubmissionStatus.SUBMITTED)
        self.assertEqual(hw_map[hw1.id]['files_count'], 1)
        self.assertEqual(hw_map[hw2.id]['submission_status'], 'NOT_STARTED')

    def test_student_report_cards_listing_and_arrears_gate(self):
        """ACD-013, ACD-014: Report cards list and arrears gate withholding."""
        # Generate, approve, and publish report card
        generate_report_cards(self.class_group, self.term)
        rc = ReportCard.objects.get(student=self.student, term=self.term)
        approve_report_card(rc, actor=self.fx['teacher_user'])
        publish_report_card(rc, actor=self.fx['teacher_user'])

        # Without arrears: visible
        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/report-cards/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data['results']), 1)
        self.assertTrue(data['results'][0]['visible'])
        self.assertEqual(data['results'][0]['status'], ReportCardStatus.PUBLISHED)

        # Enable arrears gate for school
        set_arrears_gate(self.school, True, actor=self.fx['teacher_user'])

        # Create an overdue invoice for the student
        Invoice.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            number="INV/SCH/2026/000001",
            period="2026-07",
            subtotal=Decimal('500000.00'),
            total=Decimal('500000.00'),
            paid=Decimal('0.00'),
            due_date=datetime.date.today() - datetime.timedelta(days=10),
            status=InvoiceStatus.ISSUED,
        )

        res_gated = self.client.get(f'/api/v1/academic/students/{self.student.id}/report-cards/')
        self.assertEqual(res_gated.status_code, 200)
        gated_data = res_gated.json()
        self.assertEqual(len(gated_data['results']), 1)
        self.assertFalse(gated_data['results'][0]['visible'])
        self.assertEqual(gated_data['results'][0]['reason'], 'ARREARS')

    def test_student_timetable_with_substitution(self):
        """Student timetable returns scheduled slots and reflects teacher substitutions."""
        slot = TimetableSlot.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.class_subject,
            day_of_week=DayOfWeek.MONDAY,
            period_no=1,
            start_time=datetime.time(7, 30),
            end_time=datetime.time(8, 50),
            room="Lab Komputer",
        )

        sub_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101018888",
            full_name="Pak Bambang Guru Pengganti",
        )
        sub_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628118888888",
            email="bambang@cendekia.sch.id",
            full_name="Pak Bambang Guru Pengganti",
        )
        sub_teacher = Staff.all_tenants.create(
            foundation_id=self.foundation.id,
            person=sub_person,
            user=sub_user,
            school=self.school,
            nip="198901012015011002",
            employment_type=Staff.TYPE_PERMANENT,
            join_date=datetime.date(2021, 1, 1),
            status=Staff.STATUS_ACTIVE,
        )

        today_str = datetime.date.today().isoformat()
        TimetableSubstitution.objects.create(
            foundation_id=self.foundation.id,
            slot=slot,
            date=datetime.date.today(),
            original_teacher=self.fx['teacher'],
            substitute_teacher=sub_teacher,
            status=SubstitutionStatus.ACCEPTED,
        )

        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/timetable/?date={today_str}')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(len(data['slots']), 1)
        s = data['slots'][0]
        self.assertEqual(s['subject_name'], "Matematika")
        self.assertEqual(s['room'], "Lab Komputer")
        self.assertTrue(s['is_substituted'])
        self.assertEqual(s['substitute_teacher_name'], "Pak Bambang Guru Pengganti")

    def test_cross_tenant_isolation(self):
        """Cross-tenant requests to student academic endpoints return 404."""
        fnd2 = Foundation.objects.create(
            legal_name="Yayasan Lain",
            brand_name="Yayasan Lain",
            npwp="01.222.333.4-000.000",
            address="Bandung",
        )
        other_user = User.objects.create(
            foundation_id=fnd2.id,
            phone_e164="+6289999999999",
            email="other@yayasanlain.sch.id",
            full_name="Other User",
        )
        assign_role(
            user=other_user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=fnd2.id,
            foundation_id=fnd2.id,
        )

        self.client.force_authenticate(user=other_user)
        for endpoint in ['grades', 'homework', 'report-cards', 'timetable']:
            res = self.client.get(f'/api/v1/academic/students/{self.student.id}/{endpoint}/')
            self.assertEqual(res.status_code, 404, f"Endpoint {endpoint} must return 404 cross-tenant")

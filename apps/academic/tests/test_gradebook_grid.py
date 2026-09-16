"""Tests for Teacher Gradebook Grid & Merge Prompt Modal (ACD-006, TCH-005, TCH-006, TCH-007, spec/17)."""
import datetime
from decimal import Decimal
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.academic.models import (
    AcademicYear,
    Assessment,
    AssessmentScore,
    AssessmentType,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    School,
    Subject,
    Term,
)
from apps.academic.services import (
    ScoreConflictError,
    set_assessment_score,
)
from apps.identity.models import Foundation, Person, Staff, Student, User
from apps.identity.rbac import assign_role, ROLE_TEACHER, SCOPE_SCHOOL
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class GradebookGridTests(APITestCase):
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
            full_name="Ibu Siti Rahayu",
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
                full_name="Ibu Siti Rahayu",
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
                end_date=timezone.now().date() + datetime.timedelta(days=365),
            )
            self.term = Term.objects.create(
                foundation_id=self.foundation.id,
                academic_year=self.academic_year,
                name="Ganjil",
                term_no=1,
                start_date=timezone.now().date(),
                end_date=timezone.now().date() + datetime.timedelta(days=180),
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

            # Create enrolled students
            self.students = []
            for i in range(1, 4):
                p = Person.objects.create(
                    foundation_id=self.foundation.id,
                    full_name=f"Siswa {i}",
                )
                st = Student.objects.create(
                    foundation_id=self.foundation.id,
                    school=self.school,
                    person=p,
                    nis=f"100{i}",
                )
                ClassEnrollment.objects.create(
                    foundation_id=self.foundation.id,
                    class_group=self.class_group,
                    student=st,
                    enrolled_at=timezone.now().date(),
                    is_active=True,
                )
                self.students.append(st)

            # Create 2 assessments
            self.assessment1 = Assessment.objects.create(
                foundation_id=self.foundation.id,
                class_subject=self.class_subject,
                title="Tugas 1 - Aljabar",
                type=AssessmentType.FORMATIVE,
                max_score=Decimal('100.00'),
                weight=Decimal('20.00'),
            )
            self.assessment2 = Assessment.objects.create(
                foundation_id=self.foundation.id,
                class_subject=self.class_subject,
                title="Ulangan Harian 1",
                type=AssessmentType.SUMMATIVE,
                max_score=Decimal('100.00'),
                weight=Decimal('40.00'),
            )

    def test_score_conflict_error_attributes(self):
        """ScoreConflictError carries current_graded_by and current_graded_at (TCH-007)."""
        with tenant_context(self.foundation.id):
            score1 = set_assessment_score(
                assessment=self.assessment1,
                student=self.students[0],
                score=Decimal('80.00'),
                actor=self.teacher_user,
            )
            self.assertEqual(score1.version, 1)

            # Bump to version 2 by another teacher
            other_teacher_user = User.all_tenants.create_user(
                phone_e164="+6281100000002",
                foundation_id=self.foundation.id,
                full_name="Pak Budi Santoso",
            )
            score2 = set_assessment_score(
                assessment=self.assessment1,
                student=self.students[0],
                score=Decimal('85.00'),
                actor=other_teacher_user,
                expected_version=1,
            )
            self.assertEqual(score2.version, 2)

            # Trying to write with expected_version=1 raises ScoreConflictError
            with self.assertRaises(ScoreConflictError) as ctx:
                set_assessment_score(
                    assessment=self.assessment1,
                    student=self.students[0],
                    score=Decimal('90.00'),
                    actor=self.teacher_user,
                    expected_version=1,
                )

            err = ctx.exception
            self.assertEqual(err.current_score, Decimal('85.00'))
            self.assertEqual(err.current_version, 2)
            self.assertEqual(err.current_graded_by, "Pak Budi Santoso")
            self.assertIsNotNone(err.current_graded_at)

    def test_assessment_scores_api_409_payload(self):
        """PUT /assessments/:id/scores/ returns 409 Conflict with full metadata on stale expected_version."""
        self.client.force_authenticate(user=self.teacher_user)
        with tenant_context(self.foundation.id):
            # First write: creates score (version 1)
            res1 = self.client.put(
                f'/api/v1/academic/assessments/{self.assessment1.id}/scores/',
                {'scores': [{'student_id': self.students[0].id, 'score': '75.00'}]},
                format='json',
            )
            self.assertEqual(res1.status_code, status.HTTP_200_OK)

            # Second write: Teacher A bumps to version 2
            res2 = self.client.put(
                f'/api/v1/academic/assessments/{self.assessment1.id}/scores/',
                {'scores': [{'student_id': self.students[0].id, 'score': '80.00', 'expected_version': 1}]},
                format='json',
            )
            self.assertEqual(res2.status_code, status.HTTP_200_OK)

            # Third write: Teacher B still holding expected_version 1 gets 409
            res3 = self.client.put(
                f'/api/v1/academic/assessments/{self.assessment1.id}/scores/',
                {'scores': [{'student_id': self.students[0].id, 'score': '70.00', 'expected_version': 1}]},
                format='json',
            )
            self.assertEqual(res3.status_code, status.HTTP_409_CONFLICT)
            conflict = res3.json().get('conflict', {})
            self.assertEqual(conflict['student_id'], self.students[0].id)
            self.assertEqual(conflict['current_score'], '80.00')
            self.assertEqual(conflict['current_version'], 2)
            self.assertEqual(conflict['current_graded_by'], "Ibu Siti Rahayu")
            self.assertIn('current_graded_at', conflict)

    def test_gradebook_matrix_api_includes_version(self):
        """GET /gradebook/ returns version for each score cell (ACD-006, TCH-005)."""
        self.client.force_authenticate(user=self.teacher_user)
        with tenant_context(self.foundation.id):
            # Create a score for student 0
            set_assessment_score(
                assessment=self.assessment1,
                student=self.students[0],
                score=Decimal('92.00'),
                actor=self.teacher_user,
            )

            res = self.client.get(f'/api/v1/academic/gradebook/?class_subject_id={self.class_subject.id}')
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            body = res.json()
            self.assertIn('students', body)
            self.assertIn('assessments', body)
            self.assertIn('scores', body)

            # Check scored cell
            scored = next(
                s for s in body['scores']
                if s['student_id'] == self.students[0].id and s['assessment_id'] == self.assessment1.id
            )
            self.assertEqual(scored['score'], '92.00')
            self.assertEqual(scored['version'], 1)

            # Check unscored cell (should have version 0)
            unscored = next(
                s for s in body['scores']
                if s['student_id'] == self.students[1].id and s['assessment_id'] == self.assessment1.id
            )
            self.assertIsNone(unscored['score'])
            self.assertEqual(unscored['version'], 0)

    def test_cross_tenant_isolation_returns_404(self):
        """Assert cross-tenant 404 isolation for gradebook and scores endpoints (Layer 3 tenancy)."""
        with tenant_context(self.other_foundation.id):
            other_school = School.objects.create(
                foundation_id=self.other_foundation.id,
                name="Sekolah Yayasan Lain",
                npsn="99991111",
                level=School.LEVEL_SMA,
            )
            other_user = User.all_tenants.create_user(
                phone_e164="+6281199990001",
                foundation_id=self.other_foundation.id,
                full_name="Guru Yayasan B",
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
            # Attempt to access gradebook belonging to self.foundation
            res_gradebook = self.client.get(
                f'/api/v1/academic/gradebook/?class_subject_id={self.class_subject.id}'
            )
            self.assertEqual(res_gradebook.status_code, status.HTTP_404_NOT_FOUND)

            # Attempt to modify assessment scores belonging to self.foundation
            res_scores = self.client.put(
                f'/api/v1/academic/assessments/{self.assessment1.id}/scores/',
                {'scores': [{'student_id': 99999, 'score': '80'}]},
                format='json',
            )
            self.assertEqual(res_scores.status_code, status.HTTP_404_NOT_FOUND)

    def test_template_component_rendering(self):
        """Verify gradebook_grid.html renders spreadsheet layout, attributes, and merge modal."""
        matrix_rows = [
            {
                'student': {
                    'id': self.students[0].id,
                    'nis': self.students[0].nis,
                    'person': {'full_name': 'Andi Wijaya'},
                },
                'scores': [
                    {
                        'assessment_id': self.assessment1.id,
                        'assessment_title': self.assessment1.title,
                        'score': '85.00',
                        'version': 1,
                        'descriptor': 'Sangat Baik',
                    },
                    {
                        'assessment_id': self.assessment2.id,
                        'assessment_title': self.assessment2.title,
                        'score': '90.00',
                        'version': 1,
                        'descriptor': 'Sangat Baik',
                    },
                ],
                'final_grade': '88.33',
            },
            {
                'student': {
                    'id': self.students[1].id,
                    'nis': self.students[1].nis,
                    'person': {'full_name': 'Budi Cahyono'},
                },
                'scores': [
                    {
                        'assessment_id': self.assessment1.id,
                        'assessment_title': self.assessment1.title,
                        'score': '',
                        'version': 0,
                        'descriptor': '',
                    },
                    {
                        'assessment_id': self.assessment2.id,
                        'assessment_title': self.assessment2.title,
                        'score': '',
                        'version': 0,
                        'descriptor': '',
                    },
                ],
                'final_grade': '—',
            },
        ]

        context = {
            'class_subject': self.class_subject,
            'assessments': [self.assessment1, self.assessment2],
            'matrix_rows': matrix_rows,
        }

        rendered = render_to_string('components/gradebook_grid.html', context)

        # Basic table elements
        self.assertIn('gradebook-grid-table', rendered)
        self.assertIn('Buku Nilai Guru', rendered)
        self.assertIn('Matematika', rendered)
        self.assertIn('X-IPA-1', rendered)
        self.assertIn('Andi Wijaya', rendered)
        self.assertIn('Budi Cahyono', rendered)
        self.assertIn('Tugas 1 - Aljabar', rendered)
        self.assertIn('Ulangan Harian 1', rendered)
        self.assertIn('88.33', rendered)

        # Data cell attributes for keyboard navigation & autosave
        self.assertIn('data-assessment-id', rendered)
        self.assertIn('data-student-id', rendered)
        self.assertIn('data-version="1"', rendered)
        self.assertIn('value="85.00"', rendered)
        self.assertIn('data-row="0"', rendered)
        self.assertIn('data-col="0"', rendered)

        # Status icon elements (TCH-006)
        self.assertIn('cell-icon-saving', rendered)
        self.assertIn('cell-icon-saved', rendered)
        self.assertIn('cell-icon-error', rendered)
        self.assertIn('cell-icon-conflict', rendered)

        # Merge-prompt modal dialog elements (TCH-007, spec/17 §7.3)
        self.assertIn('gradebook-merge-modal', rendered)
        self.assertIn('Konflik Penyuntingan Nilai', rendered)
        self.assertIn('Nilai Anda (Input Lokal)', rendered)
        self.assertIn('Nilai di Server', rendered)
        self.assertIn('modal-btn-override', rendered)
        self.assertIn('modal-btn-accept-server', rendered)
        self.assertIn('modal-btn-cancel', rendered)
        self.assertIn('Gunakan Nilai Saya (Timpa)', rendered)
        self.assertIn('Gunakan Nilai Server', rendered)

        # Keyboard event handling script
        self.assertIn('Enter', rendered)
        self.assertIn('Tab', rendered)
        self.assertIn('ArrowDown', rendered)
        self.assertIn('ArrowUp', rendered)
        self.assertIn('Escape', rendered)

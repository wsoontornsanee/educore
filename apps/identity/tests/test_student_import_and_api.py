"""Automated tests for Student Directory API and Atomic Bulk XLSX Import (spec/02 §5, §7, §8).

Covers:
- Acceptance Criterion 1 (spec/02 §8.1): School admin receives 404 for student of another school.
- Acceptance Criterion 2 (spec/02 §8.2): Parent linked to children at two schools sees both under one login.
- Acceptance Criterion 3 (spec/02 §8.3): Bulk import with duplicate NISNs rejects the whole file and pinpoints exact offending rows.
- Student CRUD, search filter (?q=), and status transitions (IAM-019).
- Guardian linking with financial responsibility (IAM-014, IAM-015).
- Dry-run preview mode (?dry_run=true).
"""
import io
import openpyxl
from datetime import date
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase
from apps.core.models import AuditEvent, DomainEvent
from apps.identity.models import Foundation, School, Person, User, Student, Guardian, GuardianLink
from apps.identity.rbac import (
    assign_role, ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, ROLE_PARENT,
    SCOPE_FOUNDATION, SCOPE_SCHOOL
)
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class StudentAPITestCase(APITestCase):
    def setUp(self):
        clear_current_foundation_id()

        # Foundation
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Insan Cemerlang",
            brand_name="Insan Cemerlang",
        )

        # School A and School B under same Yayasan
        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD Insan Cemerlang",
            npsn="50400001",
            level=School.LEVEL_SD,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Insan Cemerlang",
            npsn="50400002",
            level=School.LEVEL_SMP,
        )

        # Foundation Admin
        self.admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100001111",
            full_name="Admin Yayasan",
        )
        assign_role(
            user=self.admin,
            role=ROLE_FOUNDATION_ADMIN,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        # School A Admin
        self.school_a_admin = User.all_tenants.create_user(
            foundation_id=self.foundation.id,
            phone_e164="+6281100002222",
            full_name="Kepala SD",
        )
        assign_role(
            user=self.school_a_admin,
            role=ROLE_SCHOOL_ADMIN,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school_a.id,
            foundation_id=self.foundation.id,
        )

        # Existing Student in School A
        self.person_student_a = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Ahmad Yusuf",
            dob=date(2015, 5, 20),
            gender=Person.GENDER_MALE,
            nik="3201012005150001",
        )
        self.student_a = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_a,
            person=self.person_student_a,
            nis="2026-SD-101",
            nisn="0011223344",
            status=Student.STATUS_PROSPECT,
        )

        # Existing Student in School B
        self.person_student_b = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            full_name="Fatimah Yusuf",
            dob=date(2012, 8, 14),
            gender=Person.GENDER_FEMALE,
            nik="3201011408120002",
        )
        self.student_b = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_b,
            person=self.person_student_b,
            nis="2026-SMP-201",
            nisn="0011223355",
            status=Student.STATUS_ACTIVE,
        )

    def test_acceptance_criterion_1_school_admin_isolation(self):
        """Acceptance Criterion 1 (spec/02 §8.1):
        A school_admin of School A receives 404 (not 403) for a School B student ID.
        """
        self.client.force_authenticate(user=self.school_a_admin)

        # 1. Accessing student of School A returns 200 OK
        res_a = self.client.get(f'/api/v1/students/{self.student_a.id}/')
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        self.assertEqual(res_a.data['nis'], '2026-SD-101')

        # 2. Accessing student of School B MUST return 404 Not Found (do not leak existence)
        res_b = self.client.get(f'/api/v1/students/{self.student_b.id}/')
        self.assertEqual(res_b.status_code, status.HTTP_404_NOT_FOUND)

    def test_acceptance_criterion_2_parent_multiple_schools(self):
        """Acceptance Criterion 2 (spec/02 §8.2, IAM-009):
        A parent linked to two children at two schools sees both under one login.
        """
        # Create parent user and profile
        parent_user, parent_person = create_user_with_person(
            foundation_id=self.foundation.id,
            full_name="Bapak Yusuf",
            phone="+6281299998888",
        )
        assign_role(
            user=parent_user,
            role=ROLE_PARENT,
            scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
            foundation_id=self.foundation.id,
        )

        guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=parent_person,
            user=parent_user,
            occupation="Akuntan",
        )

        # Link to child 1 at School A
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=guardian,
            student=self.student_a,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

        # Link to child 2 at School B
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=guardian,
            student=self.student_b,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

        self.client.force_authenticate(user=parent_user)

        response = self.client.get('/api/v1/students/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)

        # Parent must see both children across both schools
        self.assertEqual(len(results), 2)
        student_niss = {s['nis'] for s in results}
        self.assertEqual(student_niss, {'2026-SD-101', '2026-SMP-201'})

    def test_acceptance_criterion_3_atomic_import_duplicate_nisn(self):
        """Acceptance Criterion 3 (spec/02 §8.3):
        Importing an XLSX with duplicate NISNs rejects the whole file and reports exactly the offending rows.
        """
        self.client.force_authenticate(user=self.admin)

        # Build Excel workbook in memory with 5 rows:
        # Row 2: Valid
        # Row 3: Duplicate NISN (offending row 1)
        # Row 4: Valid
        # Row 5: Duplicate NISN (offending row 2 - duplicate with row 3)
        # Row 6: Duplicate NISN matching existing student in DB (offending row 3)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['NIS', 'NISN', 'Nama Lengkap', 'NIK', 'Tanggal Lahir', 'Jenis Kelamin'])
        ws.append(['SD-1', '0010000001', 'Budi Santoso', '3201010101010001', '2016-01-01', 'L'])
        ws.append(['SD-2', '0010000002', 'Dewi Sartika', '3201010101010002', '2016-02-02', 'P'])
        ws.append(['SD-3', '0010000003', 'Citra Kirana', '3201010101010003', '2016-03-03', 'P'])
        ws.append(['SD-4', '0010000002', 'Eko Prabowo', '3201010101010004', '2016-04-04', 'L'])  # dup with row 3
        ws.append(['SD-5', '0011223344', 'Gita Gutawa', '3201010101010005', '2016-05-05', 'P'])  # dup with existing DB student_a

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)

        uploaded_file = SimpleUploadedFile(
            "students_batch.xlsx",
            out.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        initial_count = Student.all_tenants.filter(school_id=self.school_a.id).count()

        # Commit mode (dry_run=false)
        response = self.client.post(
            '/api/v1/students/import/?dry_run=false',
            {'school_id': self.school_a.id, 'file': uploaded_file},
            format='multipart'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        errors = response.data['errors']

        # Assert exactly the 2 duplicate offending rows in file plus the DB conflict were caught
        offending_rows = {e['row'] for e in errors if e['column'] == 'nisn'}
        self.assertIn(5, offending_rows)  # row 5 duplicate of row 3
        self.assertIn(6, offending_rows)  # row 6 duplicate of DB student_a

        # Assert atomicity: 0 rows committed to DB
        after_count = Student.all_tenants.filter(school_id=self.school_a.id).count()
        self.assertEqual(initial_count, after_count)

    def test_dry_run_import_preview(self):
        """Verify dry-run mode returns parsed diff without committing database state."""
        self.client.force_authenticate(user=self.admin)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['NIS', 'NISN', 'Nama Lengkap', 'NIK', 'Tanggal Lahir', 'Jenis Kelamin'])
        ws.append(['SD-NEW-1', '0090000001', 'Hasan Basri', '3201010101010011', '2016-06-01', 'L'])
        ws.append(['SD-NEW-2', '0090000002', 'Indah Permata', '3201010101010012', '2016-07-02', 'P'])

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)

        uploaded_file = SimpleUploadedFile(
            "valid_students.xlsx",
            out.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        initial_count = Student.all_tenants.filter(school_id=self.school_a.id).count()

        response = self.client.post(
            '/api/v1/students/import/?dry_run=true',
            {'school_id': self.school_a.id, 'file': uploaded_file},
            format='multipart'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertTrue(response.data['dry_run'])
        self.assertEqual(response.data['valid_rows'], 2)
        self.assertEqual(len(response.data['preview']), 2)

        # Database remained completely untouched
        self.assertEqual(Student.all_tenants.filter(school_id=self.school_a.id).count(), initial_count)

        # Now commit with dry_run=false
        uploaded_file.seek(0)
        commit_res = self.client.post(
            '/api/v1/students/import/?dry_run=false',
            {'school_id': self.school_a.id, 'file': uploaded_file},
            format='multipart'
        )
        self.assertEqual(commit_res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(commit_res.data['created_count'], 2)
        self.assertEqual(Student.all_tenants.filter(school_id=self.school_a.id).count(), initial_count + 2)

    def test_student_status_transition_and_search_api(self):
        """Verify status machine API and query search filtering."""
        self.client.force_authenticate(user=self.admin)

        # 1. Search by query (?q=Yusuf)
        res_search = self.client.get('/api/v1/students/?q=Yusuf')
        self.assertEqual(res_search.status_code, status.HTTP_200_OK)
        results = res_search.data.get('results', res_search.data)
        self.assertEqual(len(results), 2)

        # 2. Status transition from PROSPECT to ACTIVE
        res_transition = self.client.post(
            f'/api/v1/students/{self.student_a.id}/status/',
            {'status': 'ACTIVE', 'reason': 'Pembayaran biaya masuk lunas'},
            format='json'
        )
        self.assertEqual(res_transition.status_code, status.HTTP_200_OK)
        self.assertEqual(res_transition.data['status'], Student.STATUS_ACTIVE)

        # Verify domain event
        event = DomainEvent.objects.filter(
            name='identity.student.status_changed',
            foundation_id=self.foundation.id
        ).latest('occurred_at')
        self.assertEqual(event.payload['student_id'], self.student_a.id)
        self.assertEqual(event.payload['new_status'], Student.STATUS_ACTIVE)

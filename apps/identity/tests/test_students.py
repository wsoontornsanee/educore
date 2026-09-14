"""Automated tests for Student, Guardian, and GuardianLink models (spec/02 §2, §5).

Covers:
- 3-Layer tenancy query isolation (cross-tenant visibility fail-closed)
- School-scoping of student enrollment
- Guardian and GuardianLink multi-student relationships (IAM-009, IAM-014)
- Financial responsibility attribute validation (IAM-015)
- Student lifecycle state machine transitions and event publishing (IAM-019)
- Soft deletion behavior (deleted_at)
"""
from django.db import IntegrityError
from django.test import TestCase
from apps.core.models import DomainEvent
from apps.identity.models import Foundation, School, Person, User, Student, Guardian, GuardianLink
from educore.middleware.tenancy import clear_current_foundation_id, tenant_context


class StudentAndGuardianModelTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()

        # Foundation A
        self.foundation_a = Foundation.objects.create(
            legal_name="Yayasan Insan Mulia",
            brand_name="Insan Mulia",
        )
        self.school_a1 = School.all_tenants.create(
            foundation_id=self.foundation_a.id,
            name="SD Insan Mulia",
            npsn="50200001",
            level=School.LEVEL_SD,
        )
        self.school_a2 = School.all_tenants.create(
            foundation_id=self.foundation_a.id,
            name="SMP Insan Mulia",
            npsn="50200002",
            level=School.LEVEL_SMP,
        )

        # Foundation B (for tenancy cross-talk verification)
        self.foundation_b = Foundation.objects.create(
            legal_name="Yayasan Bina Bangsa",
            brand_name="Bina Bangsa",
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation_b.id,
            name="SMA Bina Bangsa",
            npsn="50200003",
            level=School.LEVEL_SMA,
        )

        # Persons under Foundation A
        self.person_student_1 = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Ahmad Dahlan",
            gender=Person.GENDER_MALE,
            nik="3201010101010001",
        )
        self.person_student_2 = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Fatimah Dahlan",
            gender=Person.GENDER_FEMALE,
            nik="3201010101010002",
        )
        self.person_guardian = Person.all_tenants.create(
            foundation_id=self.foundation_a.id,
            full_name="Budi Dahlan",
            gender=Person.GENDER_MALE,
            nik="3201010101010003",
        )

        # Guardian user account under Foundation A
        self.guardian_user = User.all_tenants.create_user(
            foundation_id=self.foundation_a.id,
            phone_e164="+6281299887766",
            full_name="Budi Dahlan",
        )

    def test_create_students_and_guardians(self):
        """Test standard creation of student, guardian, and links."""
        student_1 = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a1,
            person=self.person_student_1,
            nis="2026-SD-001",
            nisn="0012345678",
            status=Student.STATUS_PROSPECT,
        )
        student_2 = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a2,
            person=self.person_student_2,
            nis="2026-SMP-001",
            nisn="0012345679",
            status=Student.STATUS_ACTIVE,
        )

        guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation_a.id,
            person=self.person_guardian,
            user=self.guardian_user,
            occupation="Wiraswasta",
        )

        # Link 1: Father of student 1 (primary contact, financial responsible)
        link_1 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation_a.id,
            guardian=guardian,
            student=student_1,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            can_pickup=True,
            financial_responsible=True,
        )

        # Link 2: Father of student 2 (at different school under same Yayasan)
        link_2 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation_a.id,
            guardian=guardian,
            student=student_2,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            can_pickup=True,
            financial_responsible=True,
        )

        # Verify links under Foundation A tenant context
        with tenant_context(self.foundation_a.id):
            self.assertEqual(guardian.student_links.count(), 2)
            self.assertEqual(student_1.guardian_links.count(), 1)
            self.assertTrue(link_1.financial_responsible)
            self.assertEqual(link_1.relation, GuardianLink.RELATION_FATHER)

    def test_unique_student_nis_per_school(self):
        """Ensure duplicate NIS within the same school is rejected."""
        Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a1,
            person=self.person_student_1,
            nis="SD-DUP-01",
        )

        with self.assertRaises(IntegrityError):
            Student.all_tenants.create(
                foundation_id=self.foundation_a.id,
                school=self.school_a1,
                person=self.person_student_2,
                nis="SD-DUP-01",
            )

    def test_tenancy_isolation_fail_closed_and_scoped(self):
        """Verify TenantManager prevents cross-tenant visibility."""
        Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a1,
            person=self.person_student_1,
            nis="NIS-A-1",
        )

        # Without tenant context, TenantModel.objects fails closed (returns none)
        clear_current_foundation_id()
        self.assertEqual(Student.objects.count(), 0)

        # Under Foundation A context
        with tenant_context(self.foundation_a.id):
            self.assertEqual(Student.objects.count(), 1)

        # Under Foundation B context, Foundation A students are completely invisible
        with tenant_context(self.foundation_b.id):
            self.assertEqual(Student.objects.count(), 0)

    def test_student_lifecycle_state_machine(self):
        """Verify student status transitions (IAM-019) and domain events."""
        student = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a1,
            person=self.person_student_1,
            nis="2026-SD-099",
            status=Student.STATUS_PROSPECT,
        )

        # PROSPECT -> ACTIVE is valid
        student.transition_status(Student.STATUS_ACTIVE, actor_id="admin-1", reason="Enrolled")
        student.refresh_from_db()
        self.assertEqual(student.status, Student.STATUS_ACTIVE)
        self.assertEqual(student.updated_by, "admin-1")

        # Verify domain event was published
        event = DomainEvent.objects.filter(
            foundation_id=self.foundation_a.id,
            name='identity.student.status_changed'
        ).latest('occurred_at')
        self.assertEqual(event.payload['student_id'], student.id)
        self.assertEqual(event.payload['old_status'], Student.STATUS_PROSPECT)
        self.assertEqual(event.payload['new_status'], Student.STATUS_ACTIVE)

        # ACTIVE -> GRADUATED is valid
        student.transition_status(Student.STATUS_GRADUATED, actor_id="admin-1")
        student.refresh_from_db()
        self.assertEqual(student.status, Student.STATUS_GRADUATED)

        # GRADUATED -> ACTIVE is INVALID (terminal state)
        with self.assertRaises(ValueError):
            student.transition_status(Student.STATUS_ACTIVE)

    def test_soft_delete_preserves_student_record(self):
        """Verify soft deletion sets deleted_at and does not hard-delete."""
        student = Student.all_tenants.create(
            foundation_id=self.foundation_a.id,
            school=self.school_a1,
            person=self.person_student_1,
            nis="2026-SD-SOFT",
            status=Student.STATUS_ACTIVE,
        )
        student_id = student.id
        student.delete()

        # Record still exists in all_tenants.with_deleted()
        st = Student.all_tenants.with_deleted().get(id=student_id)
        self.assertIsNotNone(st.deleted_at)
        self.assertTrue(st.is_deleted)

        # Record excluded from normal all_tenants and tenant-scoped queries
        self.assertEqual(Student.all_tenants.filter(id=student_id).count(), 0)
        with tenant_context(self.foundation_a.id):
            self.assertEqual(Student.objects.filter(id=student_id).count(), 0)

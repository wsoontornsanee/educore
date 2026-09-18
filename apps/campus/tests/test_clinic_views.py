import datetime as _dt

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import ClinicOutcome, ClinicVisit, HealthProfile, MedicationStock
from apps.identity.models import Person, RoleAssignment, School, Student
from apps.identity.rbac import ROLE_CLINIC_OFFICER, ROLE_SCHOOL_ADMIN, ROLE_TEACHER
from educore.middleware.tenancy import set_current_foundation_id


def assign(fx, user, role, scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=None):
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id if scope_id is not None else fx['school'].id,
    )


class ClinicVisitViewSetTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_create_clinic_visit_as_clinic_officer(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data['complaint'], 'Demam tinggi')
        # TenancyMiddleware clears the thread-local foundation context after each
        # request finishes, so re-establish it before querying the tenant-scoped
        # manager directly (matches the pattern used elsewhere in this test suite).
        set_current_foundation_id(self.fx['foundation'].id)
        self.assertTrue(ClinicVisit.objects.filter(student=self.fx['student']).exists())

    def test_create_clinic_visit_rejects_bad_outcome(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': 'RESTING_IN_UKS',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_without_clinic_write_cannot_create(self):
        other_person_user = self.fx['teacher_user']
        RoleAssignment.all_tenants.filter(user=other_person_user, role=ROLE_CLINIC_OFFICER).delete()
        assign(self.fx, other_person_user, ROLE_TEACHER)
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class StudentMedicalAlertViewTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=_dt.date(2026, 7, 1), is_active=True,
        )
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            allergies=['Penisilin'],
        )
        assign(self.fx, self.fx['teacher_user'], ROLE_TEACHER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_teacher_sees_alert_flag_and_allergies_when_policy_enabled(self):
        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data['allergies'], ['Penisilin'])

    def test_teacher_sees_flag_only_when_policy_disabled(self):
        from apps.campus.services_clinic import get_or_create_clinic_policy
        policy = get_or_create_clinic_policy(self.fx['school'])
        policy.teacher_sees_allergies = False
        policy.save(update_fields=['teacher_sees_allergies'])

        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data.get('allergies', []), [])

    def test_teacher_at_school_a_cannot_see_medical_alert_of_student_at_school_b(self):
        # Same foundation, different school: fx['teacher_user'] is scoped to fx['school']
        # (School A) only, and has no guardian relationship to a School B student.
        school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            name="SMP Cendekia Mandiri B",
            npsn=f"219{str(abs(hash(self.fx['school'].npsn)) % 100000).zfill(5)}",
            level=School.LEVEL_SMP,
        )
        student_b_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101010303", full_name="Budi Santoso"
        )
        student_b = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=school_b,
            person=student_b_person,
            nisn="1122334466",
            nis="B-001",
            status=Student.STATUS_ACTIVE,
        )
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=student_b,
            allergies=['Kacang'],
        )

        resp = self.client.get(f"/api/v1/campus/students/{student_b.id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class StudentHealthProfileViewCrossSchoolTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_clinic_officer_at_school_a_cannot_put_health_profile_of_student_at_school_b(self):
        # Same foundation, different school: fx['teacher_user'] holds clinic.write via a
        # school-scoped ROLE_CLINIC_OFFICER assignment on fx['school'] (School A) only, and
        # has no guardian relationship to a School B student.
        school_b = School.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            name="SMP Cendekia Mandiri B",
            npsn=f"229{str(abs(hash(self.fx['school'].npsn)) % 100000).zfill(5)}",
            level=School.LEVEL_SMP,
        )
        student_b_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101010404", full_name="Citra Lestari"
        )
        student_b = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=school_b,
            person=student_b_person,
            nisn="1122334477",
            nis="B-002",
            status=Student.STATUS_ACTIVE,
        )

        resp = self.client.put(
            f"/api/v1/campus/students/{student_b.id}/health-profile/",
            {'allergies': ['Debu']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

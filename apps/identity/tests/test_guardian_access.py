from django.test import TestCase
from rest_framework.test import APIClient
from apps.identity.models import (
    Foundation,
    Guardian,
    GuardianLink,
    Person,
    RoleAssignment,
    School,
    Student,
    User,
)
from apps.identity.guardian_access import (
    can_guardian_access_student,
    get_guardian_student_ids,
    is_staff_user,
)
from educore.middleware.tenancy import set_current_foundation_id


class GuardianAccessUnitAndApiTest(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name="Yayasan Dharma", brand_name="YD")
        set_current_foundation_id(self.foundation.id)

        self.school_a = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA A", npsn="11112222", level=School.LEVEL_SMA,
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP B", npsn="33334444", level=School.LEVEL_SMP,
        )

        # Students
        self.p_student_1 = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Budi Pratama", nik="1111111111111111",
        )
        self.student_1 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_a, person=self.p_student_1, nis="1001",
        )

        self.p_student_2 = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Siti Rahma", nik="2222222222222222",
        )
        self.student_2 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_a, person=self.p_student_2, nis="1002",
        )

        self.p_student_3 = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Agus Susanto", nik="3333333333333333",
        )
        self.student_3 = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_b, person=self.p_student_3, nis="2001",
        )

        # Parent 1: Linked to student_1 (financial) and student_3 (non-financial)
        self.user_parent_1 = User.all_tenants.create_user(
            phone_e164="+628111000001",
            password="password123",
            foundation_id=self.foundation.id,
            full_name="Wali 1",
        )
        self.p_parent_1 = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Wali Murid 1", nik="4444444444444444",
        )
        self.guardian_1 = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=self.p_parent_1, user=self.user_parent_1,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_parent_1,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )
        self.link_1 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian_1,
            student=self.student_1,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
        )
        self.link_3 = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian_1,
            student=self.student_3,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=False,
        )

        # Staff: Teacher at school_a
        self.user_teacher = User.all_tenants.create_user(
            phone_e164="+628111000002",
            password="password123",
            foundation_id=self.foundation.id,
            full_name="Guru 1",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_teacher,
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )

        # Dual-role: Teacher at School A and Parent of student_3 at School B
        self.user_dual = User.all_tenants.create_user(
            phone_e164="+628111000003",
            password="password123",
            foundation_id=self.foundation.id,
            full_name="Guru Dual",
        )
        self.p_dual = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Guru Sekaligus Wali", nik="5555555555555555",
        )
        self.guardian_dual = Guardian.all_tenants.create(
            foundation_id=self.foundation.id, person=self.p_dual, user=self.user_dual,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_dual,
            role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_a.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.user_dual,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.school_b.id,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian_dual,
            student=self.student_3,
            relation=GuardianLink.RELATION_MOTHER,
            financial_responsible=True,
        )

        self.client = APIClient()

    def tearDown(self):
        set_current_foundation_id(None)

    def test_guardian_access_primitives(self):
        # Parent 1 linked students
        linked = get_guardian_student_ids(self.user_parent_1, self.foundation.id)
        self.assertEqual(linked, {self.student_1.id, self.student_3.id})

        # Financial only
        financial_linked = get_guardian_student_ids(self.user_parent_1, self.foundation.id, financial_only=True)
        self.assertEqual(financial_linked, {self.student_1.id})

        # Staff checks
        self.assertFalse(is_staff_user(self.user_parent_1, self.foundation.id))
        self.assertTrue(is_staff_user(self.user_teacher, self.foundation.id))
        self.assertTrue(is_staff_user(self.user_teacher, self.foundation.id, school_id=self.school_a.id))
        self.assertFalse(is_staff_user(self.user_teacher, self.foundation.id, school_id=self.school_b.id))

        # Access check
        self.assertTrue(can_guardian_access_student(self.user_parent_1, self.student_1.id, self.foundation.id))
        self.assertFalse(can_guardian_access_student(self.user_parent_1, self.student_2.id, self.foundation.id))
        self.assertTrue(can_guardian_access_student(self.user_teacher, self.student_1.id, self.foundation.id))
        self.assertTrue(can_guardian_access_student(self.user_teacher, self.student_2.id, self.foundation.id))
        self.assertFalse(can_guardian_access_student(self.user_teacher, self.student_3.id, self.foundation.id))

    def test_student_viewset_parent_isolation(self):
        self.client.force_authenticate(user=self.user_parent_1)
        resp = self.client.get('/api/v1/students/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {item['id'] for item in resp.data['results']}
        # Parent 1 must only see student_1 and student_3, never student_2 (IAM-014)
        self.assertIn(self.student_1.id, returned_ids)
        self.assertIn(self.student_3.id, returned_ids)
        self.assertNotIn(self.student_2.id, returned_ids)

        # Retrieve own child: 200
        resp_detail_own = self.client.get(f'/api/v1/students/{self.student_1.id}/')
        self.assertEqual(resp_detail_own.status_code, 200)

        # Retrieve other child: 404
        resp_detail_other = self.client.get(f'/api/v1/students/{self.student_2.id}/')
        self.assertEqual(resp_detail_other.status_code, 404)

    def test_student_viewset_dual_role(self):
        # Teacher at School A, Parent at School B
        self.client.force_authenticate(user=self.user_dual)
        resp = self.client.get('/api/v1/students/')
        self.assertEqual(resp.status_code, 200)
        returned_ids = {item['id'] for item in resp.data['results']}
        # Sees all students in School A (student_1, student_2) because of teacher role
        self.assertIn(self.student_1.id, returned_ids)
        self.assertIn(self.student_2.id, returned_ids)
        # Sees own child in School B (student_3)
        self.assertIn(self.student_3.id, returned_ids)

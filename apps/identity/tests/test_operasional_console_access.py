"""Access gate shared by the Operasional console pages (Kehadiran & gerbang,
Kantin & dompet, Mode ujian): permission key + Staff profile + school scope +
cross-tenant 404, via apps.identity.console_access.
"""
import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.console_access import permitted_schools
from apps.identity.models import Person, RoleAssignment, School, Staff, User
from educore.middleware.tenancy import set_current_foundation_id

# page url -> the role that legitimately holds that page's permission key
PAGES = {
    '/web/attendance/gate/': 'teacher',           # attendance.read
    '/web/wallet/canteen/': 'canteen_operator',   # wallet.topup.read
    '/web/academic/exams/': 'teacher',            # grades.read
}


def make_staff_user(fx, role, scope_type, scope_id, tag):
    """A Staff-profiled user holding `role` at the given scope."""
    foundation = fx['foundation']
    person = Person.all_tenants.create(foundation_id=foundation.id, nik=f"31710100000{tag}", full_name=f"Staf {tag}")
    user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+62899{tag}", email=f"staf{tag}@console.test", full_name=f"Staf {tag}",
    )
    Staff.all_tenants.create(
        foundation_id=foundation.id, person=person, user=user, school=fx['school'],
        nip=f"1990{tag}", employment_type=Staff.TYPE_PERMANENT, join_date=datetime.date(2021, 1, 1),
        status=Staff.STATUS_ACTIVE,
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    return user


class PermittedSchoolsTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Akses Konsol")
        self.foundation = self.fx['foundation']
        set_current_foundation_id(self.foundation.id)
        self.school_a = self.fx['school']
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Akses", npsn="60000002", level=School.LEVEL_SMP,
        )

    def test_school_scoped_role_sees_only_its_school(self):
        user = make_staff_user(self.fx, 'teacher', RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '01')
        ids = [s.id for s in permitted_schools(user, self.foundation.id, 'attendance.read')]
        self.assertEqual(ids, [self.school_a.id])

    def test_foundation_scoped_role_sees_every_school(self):
        user = make_staff_user(self.fx, 'foundation_admin', RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, '02')
        ids = {s.id for s in permitted_schools(user, self.foundation.id, 'attendance.read')}
        self.assertEqual(ids, {self.school_a.id, self.school_b.id})

    def test_role_without_the_permission_sees_no_school(self):
        # teacher does not hold wallet.topup.read
        user = make_staff_user(self.fx, 'teacher', RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '03')
        self.assertEqual(list(permitted_schools(user, self.foundation.id, 'wallet.topup.read')), [])


class OperasionalConsoleAccessTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Akses Halaman")
        self.foundation = self.fx['foundation']
        set_current_foundation_id(self.foundation.id)
        self.school_a = self.fx['school']
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Halaman", npsn="60000003", level=School.LEVEL_SMP,
        )
        self.client = APIClient()

    def _staff(self, role, school, tag):
        return make_staff_user(self.fx, role, RoleAssignment.SCOPE_SCHOOL, school.id, tag)

    def test_unauthenticated_is_rejected(self):
        for url in PAGES:
            self.assertIn(self.client.get(url).status_code, (401, 403), url)

    def test_staff_with_permission_gets_the_page(self):
        for i, (url, role) in enumerate(PAGES.items()):
            self.client.force_authenticate(user=self._staff(role, self.school_a, f'1{i}'))
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_guardian_without_staff_profile_gets_404(self):
        # ROLE_PARENT holds attendance.read / grades.read / wallet.topup.read
        _guardian, guardian_user = make_guardian(
            self.foundation, self.school_a, "+628133000777", "wali@akses.test", "Wali Akses", "3471010101023001",
            student=self.fx['student'],
        )
        self.client.force_authenticate(user=guardian_user)
        for url in PAGES:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_role_without_the_page_permission_gets_403(self):
        # clinic_officer holds none of attendance.read / grades.read / wallet.topup.read
        self.client.force_authenticate(user=self._staff('clinic_officer', self.school_a, '20'))
        for url in PAGES:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_school_scoped_staff_cannot_open_another_school(self):
        # HasRequiredPermission (IAM-012) already denies a school the role isn't assigned to.
        for i, (url, role) in enumerate(PAGES.items()):
            self.client.force_authenticate(user=self._staff(role, self.school_a, f'3{i}'))
            self.assertEqual(self.client.get(url, {'school_id': self.school_b.id}).status_code, 403, url)

    def test_malformed_school_id_is_404(self):
        for i, (url, role) in enumerate(PAGES.items()):
            self.client.force_authenticate(user=self._staff(role, self.school_a, f'4{i}'))
            self.assertEqual(self.client.get(url, {'school_id': 'abc'}).status_code, 404, url)

    def test_foundation_scoped_staff_gets_404_for_another_tenants_school(self):
        other = build_academic_fixture("Yayasan Lain Sekali")
        set_current_foundation_id(self.foundation.id)
        for i, url in enumerate(PAGES):
            user = make_staff_user(self.fx, 'foundation_admin', RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, f'5{i}')
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get(url, {'school_id': other['school'].id}).status_code, 404, url)

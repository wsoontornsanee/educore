"""RPT-009: WHICH students a metering count was made of, captured with the count and frozen with it."""
import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.reporting.models import RptActiveStudent, RptActiveStudentRoster
from apps.reporting.services import (
    METERING_FROZEN, METERING_NOT_COMPUTED, METERING_OPEN, get_metering_roster, get_metering_statement,
    refresh_active_students,
)
from apps.reporting.views import _schools_allowed_for_all

NOW = timezone.now()
THIS_MONTH = NOW.date().replace(day=1)
LAST_MONTH = (THIS_MONTH - datetime.timedelta(days=1)).replace(day=1)


class _Base(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.enrolled = [self.fx['student']]
        self._enroll(self.fx['student'])

    def _enroll(self, student):
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=student, class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def add_student(self, name, nis, enrolled=True, school=None):
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=school or self.school, person=person, nis=nis,
            nisn='0099887766', status=Student.STATUS_ACTIVE,
        )
        if enrolled:
            self._enroll(student)
        return student

    def roster(self, month=THIS_MONTH, school=None):
        return RptActiveStudentRoster.all_tenants.get(
            foundation_id=self.foundation.id, school=school or self.school, month=month,
        )


class RosterCaptureTests(_Base):
    def test_the_roster_holds_exactly_the_students_the_count_was_made_of(self):
        second = self.add_student('Budi Santoso', '2026002')
        self.add_student('Tidak Terdaftar', '2026003', enrolled=False)  # not in a class: not counted
        refresh_active_students(scope='dashboard')
        count = RptActiveStudent.all_tenants.get(foundation_id=self.foundation.id, school=self.school, month=THIS_MONTH)
        roster = self.roster()
        self.assertEqual(roster.student_ids, sorted([self.fx['student'].id, second.id]))
        self.assertEqual(count.active_count, len(roster.student_ids))
        self.assertEqual(roster.captured_at, count.computed_at)

    def test_an_open_month_roster_follows_each_refresh(self):
        refresh_active_students(scope='dashboard')
        added = self.add_student('Siti Aminah', '2026004')
        refresh_active_students(scope='dashboard')
        self.assertIn(added.id, self.roster().student_ids)
        self.assertEqual(RptActiveStudentRoster.all_tenants.filter(school=self.school, month=THIS_MONTH).count(), 1)

    def test_a_closed_month_roster_is_frozen_with_its_count(self):
        refresh_active_students(scope='full', since=LAST_MONTH)
        frozen_ids = list(self.roster(LAST_MONTH).student_ids)
        self.add_student('Terlambat Masuk', '2026005')
        refresh_active_students(scope='full', since=LAST_MONTH)
        self.assertEqual(self.roster(LAST_MONTH).student_ids, frozen_ids)
        count = RptActiveStudent.all_tenants.get(foundation_id=self.foundation.id, school=self.school, month=LAST_MONTH)
        self.assertEqual(count.active_count, len(frozen_ids))

    def test_a_month_frozen_before_rosters_existed_gets_no_invented_roster(self):
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, month=LAST_MONTH, active_count=7, computed_at=NOW,
        )
        refresh_active_students(scope='full', since=LAST_MONTH)
        self.assertFalse(RptActiveStudentRoster.all_tenants.filter(school=self.school, month=LAST_MONTH).exists())
        count = RptActiveStudent.all_tenants.get(foundation_id=self.foundation.id, school=self.school, month=LAST_MONTH)
        self.assertEqual(count.active_count, 7)  # untouched: RPT-008

    def test_the_count_and_the_roster_are_written_together_or_not_at_all(self):
        with mock.patch.object(
            RptActiveStudentRoster.all_tenants, 'update_or_create', side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                refresh_active_students(scope='dashboard')
        self.assertFalse(RptActiveStudent.all_tenants.filter(school=self.school, month=THIS_MONTH).exists())


class MeteringRosterServiceTests(_Base):
    def test_lists_counted_students_with_name_nis_and_current_status_but_never_nisn(self):
        self.add_student('Budi Santoso', '2026002')
        refresh_active_students(scope='dashboard')
        result = get_metering_roster(self.foundation.id, self.school, THIS_MONTH)
        self.assertTrue(result['roster_available'])
        self.assertEqual(result['state'], METERING_OPEN)
        self.assertEqual(result['active_count'], 2)
        self.assertEqual(len(result['students']), 2)
        for row in result['students']:
            self.assertEqual(set(row), {'student_id', 'full_name', 'nis', 'current_status'})
        self.assertNotIn('nisn', str(result))
        self.assertNotIn('0099887766', str(result))

    def test_students_are_ordered_by_name(self):
        self.add_student('Aaron Awal', '2026009')
        refresh_active_students(scope='dashboard')
        names = [s['full_name'] for s in get_metering_roster(self.foundation.id, self.school, THIS_MONTH)['students']]
        self.assertEqual(names, sorted(names))

    def test_a_student_who_left_since_is_still_listed_with_their_current_status(self):
        leaver = self.add_student('Pindah Sekolah', '2026006')
        refresh_active_students(scope='dashboard')
        leaver.status = Student.STATUS_TRANSFERRED_OUT
        leaver.save()
        by_id = {s['student_id']: s for s in get_metering_roster(self.foundation.id, self.school, THIS_MONTH)['students']}
        self.assertEqual(by_id[leaver.id]['current_status'], Student.STATUS_TRANSFERRED_OUT)

    def test_a_month_with_no_roster_says_so_instead_of_returning_an_empty_list(self):
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, month=LAST_MONTH, active_count=7, computed_at=NOW,
        )
        result = get_metering_roster(self.foundation.id, self.school, LAST_MONTH)
        self.assertEqual(result['state'], METERING_FROZEN)
        self.assertEqual(result['active_count'], 7)
        self.assertFalse(result['roster_available'])
        self.assertEqual(result['students'], [])
        self.assertIsNone(result['captured_at'])

    def test_a_month_never_computed_is_not_computed(self):
        result = get_metering_roster(self.foundation.id, self.school, LAST_MONTH)
        self.assertEqual(result['state'], METERING_NOT_COMPUTED)
        self.assertIsNone(result['active_count'])
        self.assertFalse(result['roster_available'])

    def test_a_roster_never_leaks_across_foundations(self):
        other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain")
        other_school = School.all_tenants.create(
            foundation_id=other.id, name="SD Lain", npsn="70155577", level=School.LEVEL_SD, base_currency="IDR",
        )
        refresh_active_students(scope='dashboard')
        result = get_metering_roster(other.id, other_school, THIS_MONTH)
        self.assertEqual(result['students'], [])

    def test_the_statement_flags_which_schools_have_a_roster(self):
        refresh_active_students(scope='dashboard')
        second = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Dua", npsn="70155566", level=School.LEVEL_SMP, base_currency="IDR",
        )
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=second, month=THIS_MONTH, active_count=3, computed_at=NOW,
        )
        by_school = {e['school_id']: e for e in get_metering_statement(self.foundation.id, THIS_MONTH)['schools']}
        self.assertTrue(by_school[self.school.id]['roster_available'])
        self.assertFalse(by_school[second.id]['roster_available'])


class SchoolsAllowedForAllTests(TestCase):
    def check(self, ceilings):
        with mock.patch('apps.reporting.views.accessible_school_ids', side_effect=lambda u, f, p: ceilings[p]):
            return _schools_allowed_for_all(object(), 1, *ceilings)

    def test_every_permission_foundation_wide_means_every_school(self):
        self.assertIsNone(self.check({'a': None, 'b': None}))

    def test_one_school_scoped_permission_limits_to_that_set(self):
        self.assertEqual(self.check({'a': None, 'b': {1, 2}}), {1, 2})

    def test_the_intersection_is_taken_when_both_are_scoped(self):
        self.assertEqual(self.check({'a': {1, 2, 3}, 'b': {2, 3, 4}}), {2, 3})

    def test_disjoint_scopes_allow_nothing(self):
        self.assertEqual(self.check({'a': {1}, 'b': {2}}), set())


class MeteringRosterApiTests(_Base):
    URL = '/api/v1/metering/statements/roster/'

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.other_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Cabang Dua", npsn="70155599", level=School.LEVEL_SMP,
            base_currency="IDR",
        )
        self.add_student('Budi Santoso', '2026002')
        refresh_active_students(scope='dashboard')
        self.admin = self.user_with(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, '+6281200000401',
        )

    def user_with(self, role, scope_type, scope_id, phone):
        user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164=phone, full_name=f"{role} {phone}", is_active=True,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
        )
        return user

    def get(self, user, query):
        self.client.force_authenticate(user=user)
        return self.client.get(f'{self.URL}{query}')

    def test_foundation_admin_reads_a_schools_roster(self):
        res = self.get(self.admin, f'?school_id={self.school.id}&month={THIS_MONTH:%Y-%m}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['school_id'], self.school.id)
        self.assertTrue(body['roster_available'])
        self.assertEqual(len(body['students']), 2)
        self.assertEqual({s['nis'] for s in body['students']}, {self.fx['student'].nis, '2026002'})
        self.assertNotIn('nisn', res.content.decode())

    def test_month_defaults_to_the_current_month(self):
        res = self.get(self.admin, f'?school_id={self.school.id}')
        self.assertEqual(res.json()['month'], f'{THIS_MONTH:%Y-%m}')

    def test_a_school_admin_reads_only_their_own_school(self):
        school_admin = self.user_with(
            RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.SCOPE_SCHOOL, self.school.id, '+6281200000402',
        )
        self.assertEqual(self.get(school_admin, f'?school_id={self.school.id}').status_code, 200)
        # The permission layer reads school_id from the query and refuses a school outside the assignment.
        self.assertEqual(self.get(school_admin, f'?school_id={self.other_school.id}').status_code, 403)

    def test_a_teacher_without_reporting_read_is_denied(self):
        self.assertEqual(self.get(self.fx['teacher_user'], f'?school_id={self.school.id}').status_code, 403)

    def test_another_foundations_school_is_a_404(self):
        other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain")
        foreign = School.all_tenants.create(
            foundation_id=other.id, name="SD Lain", npsn="70155588", level=School.LEVEL_SD, base_currency="IDR",
        )
        self.assertEqual(self.get(self.admin, f'?school_id={foreign.id}').status_code, 404)

    def test_bad_requests(self):
        self.assertEqual(self.get(self.admin, '').status_code, 400)                                   # no school_id
        self.assertEqual(self.get(self.admin, f'?school_id={self.school.id}&month=2026-13').status_code, 400)
        self.assertEqual(self.get(self.admin, '?school_id=abc').status_code, 404)                      # not a school

    def test_a_month_with_no_roster_says_so(self):
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, month=LAST_MONTH, active_count=9, computed_at=NOW,
        )
        body = self.get(self.admin, f'?school_id={self.school.id}&month={LAST_MONTH:%Y-%m}').json()
        self.assertEqual(body['state'], METERING_FROZEN)
        self.assertEqual(body['active_count'], 9)
        self.assertFalse(body['roster_available'])
        self.assertEqual(body['students'], [])

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(f'{self.URL}?school_id={self.school.id}').status_code, (401, 403))

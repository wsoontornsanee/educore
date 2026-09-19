"""RPT-009: the metering statement: counted students per school, as the invoice basis reads them."""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.reporting.models import RptActiveStudent
from apps.reporting.services import (
    METERING_FROZEN, METERING_NOT_COMPUTED, METERING_OPEN, get_metering_statement,
)

TODAY = datetime.date(2026, 9, 19)
OPEN_MONTH = datetime.date(2026, 9, 1)
CLOSED_MONTH = datetime.date(2026, 8, 1)


def _count(foundation, school, month, active_count):
    return RptActiveStudent.all_tenants.create(
        foundation_id=foundation.id, school=school, month=month, active_count=active_count,
        computed_at=timezone.now(),
    )


class _Base(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school_a = self.fx['school']
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Cabang Dua", npsn="70155599", level=School.LEVEL_SMP,
            base_currency="IDR",
        )

    def user_with(self, role, scope_type, scope_id, phone):
        user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164=phone, full_name=f"{role} {phone}", is_active=True,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
        )
        return user


class MeteringStatementServiceTests(_Base):
    def statement(self, month=OPEN_MONTH, school_ids=None, foundation_id=None):
        return get_metering_statement(foundation_id or self.foundation.id, month, school_ids=school_ids, today=TODAY)

    def by_school(self, statement):
        return {e['school_id']: e for e in statement['schools']}

    def test_open_month_reports_counts_as_open(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 120)
        entry = self.by_school(self.statement())[self.school_a.id]
        self.assertEqual(entry['state'], METERING_OPEN)
        self.assertEqual(entry['active_count'], 120)
        self.assertIsNotNone(entry['computed_at'])

    def test_a_month_that_has_ended_is_frozen(self):
        _count(self.foundation, self.school_a, CLOSED_MONTH, 118)
        entry = self.by_school(self.statement(CLOSED_MONTH))[self.school_a.id]
        self.assertEqual(entry['state'], METERING_FROZEN)
        self.assertEqual(entry['active_count'], 118)

    def test_a_school_with_no_row_is_not_computed_never_zero(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 120)
        entry = self.by_school(self.statement())[self.school_b.id]
        self.assertEqual(entry['state'], METERING_NOT_COMPUTED)
        self.assertIsNone(entry['active_count'])
        self.assertIsNone(entry['computed_at'])

    def test_total_sums_counted_schools_and_complete_says_whether_it_is_the_whole(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 120)
        partial = self.statement()
        self.assertEqual(partial['total_active'], 120)
        self.assertFalse(partial['complete'])
        _count(self.foundation, self.school_b, OPEN_MONTH, 30)
        whole = self.statement()
        self.assertEqual(whole['total_active'], 150)
        self.assertTrue(whole['complete'])

    def test_a_zero_count_is_a_real_count_not_missing(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 0)
        _count(self.foundation, self.school_b, OPEN_MONTH, 0)
        statement = self.statement()
        self.assertTrue(statement['complete'])
        self.assertEqual(self.by_school(statement)[self.school_a.id]['active_count'], 0)

    def test_school_ids_limits_the_schools_and_the_total(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 120)
        _count(self.foundation, self.school_b, OPEN_MONTH, 30)
        statement = self.statement(school_ids={self.school_b.id})
        self.assertEqual([e['school_id'] for e in statement['schools']], [self.school_b.id])
        self.assertEqual(statement['total_active'], 30)

    def test_month_is_normalised_and_other_months_are_not_mixed_in(self):
        _count(self.foundation, self.school_a, OPEN_MONTH, 120)
        _count(self.foundation, self.school_a, CLOSED_MONTH, 99)
        statement = self.statement(datetime.date(2026, 9, 17))
        self.assertEqual(statement['month'], '2026-09')
        self.assertEqual(self.by_school(statement)[self.school_a.id]['active_count'], 120)

    def test_another_foundations_counts_never_appear(self):
        other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain")
        other_school = School.all_tenants.create(
            foundation_id=other.id, name="SD Lain", npsn="70155588", level=School.LEVEL_SD, base_currency="IDR",
        )
        _count(other, other_school, OPEN_MONTH, 999)
        statement = self.statement()
        self.assertNotIn(other_school.id, self.by_school(statement))
        self.assertEqual(statement['total_active'], 0)

    def test_reading_the_statement_never_writes_a_count(self):
        self.statement()
        self.assertFalse(RptActiveStudent.all_tenants.filter(foundation_id=self.foundation.id).exists())


class MeteringStatementApiTests(_Base):
    URL = '/api/v1/metering/statements/'

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.month = timezone.now().date().replace(day=1)
        _count(self.foundation, self.school_a, self.month, 120)
        _count(self.foundation, self.school_b, self.month, 30)
        self.admin = self.user_with(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, '+6281200000301',
        )

    def get(self, user, query=''):
        self.client.force_authenticate(user=user)
        return self.client.get(f'{self.URL}{query}')

    def test_foundation_admin_sees_every_school_and_the_total(self):
        res = self.get(self.admin, f'?month={self.month:%Y-%m}')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['month'], f'{self.month:%Y-%m}')
        self.assertEqual({e['school_id'] for e in body['schools']}, {self.school_a.id, self.school_b.id})
        self.assertEqual(body['total_active'], 150)
        self.assertTrue(body['complete'])

    def test_month_defaults_to_the_current_month(self):
        self.assertEqual(self.get(self.admin).json()['month'], f'{self.month:%Y-%m}')

    def test_a_school_scoped_admin_sees_only_their_own_school(self):
        school_admin = self.user_with(
            RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.SCOPE_SCHOOL, self.school_b.id, '+6281200000302',
        )
        body = self.get(school_admin).json()
        self.assertEqual([e['school_id'] for e in body['schools']], [self.school_b.id])
        self.assertEqual(body['total_active'], 30)

    def test_a_user_without_reporting_read_is_denied(self):
        res = self.get(self.fx['teacher_user'])
        self.assertEqual(res.status_code, 403)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))

    def test_another_foundation_id_is_a_404_not_their_data(self):
        other = Foundation.objects.create(legal_name="Yayasan Lain", brand_name="Lain")
        res = self.get(self.admin, f'?foundation_id={other.id}')
        self.assertEqual(res.status_code, 404)

    def test_the_callers_own_foundation_id_is_accepted(self):
        res = self.get(self.admin, f'?foundation_id={self.foundation.id}')
        self.assertEqual(res.status_code, 200)

    def test_a_bad_month_is_a_400(self):
        for bad in ('2026-13', 'september', '2026-9-1', '26-09'):
            with self.subTest(month=bad):
                self.assertEqual(self.get(self.admin, f'?month={bad}').status_code, 400)

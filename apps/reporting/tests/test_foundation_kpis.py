import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.foundation.models import RptFoundationKPI
from apps.reporting.models import RptActiveStudent, RptArAging, RptDailyAttendance, RptDailyFinance, RptWalletActivity
from apps.reporting.services import refresh_foundation_kpis


class RefreshFoundationKpisTests(TestCase):
    """spec/03 §4, spec/15 §2: rpt_foundation_kpis must be sourced from the other
    already-refreshed rpt_* tables, never live transactional tables (FND-005)."""

    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.month = timezone.now().date().replace(day=1)
        self.now = timezone.now()

        RptDailyFinance.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=self.month,
            currency='IDR', billed=Decimal('5000000.00'), collected=Decimal('4000000.00'),
            outstanding=Decimal('1000000.00'), computed_at=self.now,
        )
        RptArAging.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            as_of=self.month, bucket='0_30', currency='IDR', amount=Decimal('600000.00'), computed_at=self.now,
        )
        RptArAging.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            as_of=self.month, bucket='31_60', currency='IDR', amount=Decimal('400000.00'), computed_at=self.now,
        )
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, month=self.month,
            active_count=120, computed_at=self.now,
        )
        RptWalletActivity.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=self.month,
            currency='IDR', purchases=Decimal('300000.00'), computed_at=self.now,
        )
        RptDailyAttendance.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, date=self.month,
            class_group=self.fx['class_group'], present=90, late=5, sick=2, permitted=1, absent=2,
            rate_pct=Decimal('95.00'), computed_at=self.now,
        )

    def test_per_school_row_aggregates_from_rollup_tables(self):
        refresh_foundation_kpis(scope='full')

        row = RptFoundationKPI.objects.get(
            foundation_id=self.foundation.id, school_id=self.school.id, period_start=self.month,
        )
        self.assertEqual(row.billed, Decimal('5000000.00'))
        self.assertEqual(row.collected, Decimal('4000000.00'))
        self.assertEqual(row.outstanding, Decimal('1000000.00'))
        self.assertEqual(row.ar_0_30, Decimal('600000.00'))
        self.assertEqual(row.ar_31_60, Decimal('400000.00'))
        self.assertEqual(row.campus_spend, Decimal('300000.00'))
        self.assertEqual(row.active_students, 120)
        self.assertEqual(row.avg_attendance_pct, Decimal('95.00'))  # (90+5)/100 * 100

    def test_foundation_aggregate_row_sums_all_schools(self):
        refresh_foundation_kpis(scope='full')

        agg = RptFoundationKPI.objects.get(
            foundation_id=self.foundation.id, school_id=None, period_start=self.month,
        )
        self.assertEqual(agg.billed, Decimal('5000000.00'))
        self.assertEqual(agg.active_students, 120)
        self.assertEqual(agg.reporting_currency, self.foundation.reporting_currency)

    def test_rerun_is_idempotent(self):
        refresh_foundation_kpis(scope='full')
        refresh_foundation_kpis(scope='full')

        self.assertEqual(
            RptFoundationKPI.objects.filter(
                foundation_id=self.foundation.id, school_id=self.school.id, period_start=self.month,
            ).count(),
            1,
        )

    def test_dashboard_scope_only_touches_current_month(self):
        old_month = (self.month - datetime.timedelta(days=200)).replace(day=1)
        old_month_end = (old_month.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        RptFoundationKPI.objects.create(
            foundation_id=self.foundation.id, school_id=self.school.id,
            period_start=old_month, period_end=old_month_end,
            billed=Decimal('1.00'), currency='IDR', reporting_currency='IDR',
        )

        refresh_foundation_kpis(scope='dashboard')

        row = RptFoundationKPI.objects.get(
            foundation_id=self.foundation.id, school_id=self.school.id, period_start=old_month,
        )
        self.assertEqual(row.billed, Decimal('1.00'))  # untouched: dashboard scope only recomputes the current month

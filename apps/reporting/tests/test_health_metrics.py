import datetime

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.reporting.health import get_health_metrics, is_declining
from apps.reporting.models import RptParentWeeklyActivity


class IsDecliningTests(SimpleTestCase):
    def test_three_strict_drops_are_flagged(self):
        self.assertTrue(is_declining([0.8, 0.6, 0.5, 0.4]))

    def test_a_tie_breaks_the_streak(self):
        self.assertFalse(is_declining([0.8, 0.6, 0.6, 0.4]))

    def test_a_rise_breaks_the_streak(self):
        self.assertFalse(is_declining([0.5, 0.6, 0.5, 0.4]))

    def test_only_three_weeks_of_data_is_not_enough(self):
        self.assertFalse(is_declining([0.6, 0.5, 0.4]))

    def test_a_missing_week_is_not_flagged(self):
        self.assertFalse(is_declining([0.8, None, 0.5, 0.4]))

    def test_only_the_last_four_weeks_matter(self):
        self.assertTrue(is_declining([0.1, 0.9, 0.7, 0.5, 0.3]))


class GetHealthMetricsTests(TestCase):
    TODAY = datetime.date(2026, 9, 16)  # a Wednesday; current week starts 2026-09-14

    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.current = datetime.date(2026, 9, 14)

    def _week(self, weeks_ago, active, enrolled=10):
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'],
            week_start=self.current - datetime.timedelta(weeks=weeks_ago),
            active_parents=active, enrolled_students=enrolled, computed_at=timezone.now(),
        )

    def _result(self, **kwargs):
        (row,) = get_health_metrics(today=self.TODAY, **kwargs)
        return row

    def test_three_consecutive_drops_over_completed_weeks_flag_the_school(self):
        for weeks_ago, active in [(4, 8), (3, 6), (2, 5), (1, 4)]:
            self._week(weeks_ago, active)
        self._week(0, 9)  # the in-progress week is ignored for the flag
        row = self._result()
        self.assertTrue(row['at_risk'])
        self.assertEqual(row['latest_wau_pct'], 40.0)
        self.assertEqual(row['school_id'], self.fx['school'].id)
        self.assertEqual(row['foundation_id'], self.fid)

    def test_a_gap_in_the_completed_weeks_is_not_flagged(self):
        for weeks_ago, active in [(4, 8), (2, 5), (1, 4)]:
            self._week(weeks_ago, active)
        self.assertFalse(self._result()['at_risk'])

    def test_weeks_are_oldest_first_and_only_the_current_one_is_incomplete(self):
        self._week(1, 4)
        self._week(0, 3)
        weeks = self._result()['weeks']
        self.assertEqual([w['week_start'] for w in weeks], ['2026-09-07', '2026-09-14'])
        self.assertEqual([w['complete'] for w in weeks], [True, False])
        self.assertEqual(weeks[0], {
            'week_start': '2026-09-07', 'active_parents': 4, 'enrolled_students': 10, 'wau_pct': 40.0, 'complete': True,
        })

    def test_a_school_with_no_enrolled_students_has_no_percentage(self):
        self._week(1, 0, enrolled=0)
        row = self._result()
        self.assertIsNone(row['weeks'][0]['wau_pct'])
        self.assertIsNone(row['latest_wau_pct'])

    def test_foundation_filter(self):
        self._week(1, 4)
        self.assertEqual(get_health_metrics(foundation_id=self.fid + 999, today=self.TODAY), [])
        self.assertEqual(len(get_health_metrics(foundation_id=self.fid, today=self.TODAY)), 1)

    def test_weeks_older_than_the_window_are_left_out(self):
        self._week(9, 5)
        self.assertEqual(get_health_metrics(today=self.TODAY), [])

    def test_latest_wau_pct_is_none_when_the_previous_week_is_missing(self):
        self._week(3, 4)
        self.assertIsNone(self._result()['latest_wau_pct'])

    def test_latest_wau_pct_is_the_previous_week_not_the_in_progress_one(self):
        self._week(1, 4)
        self._week(0, 9)
        self.assertEqual(self._result()['latest_wau_pct'], 40.0)

    def test_a_soft_deleted_school_is_left_out(self):
        self._week(1, 4)
        self.fx['school'].delete()
        self.assertEqual(get_health_metrics(today=self.TODAY), [])

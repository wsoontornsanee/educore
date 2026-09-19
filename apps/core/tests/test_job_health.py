"""Is each scheduled job still succeeding? (spec/01 ARC-008)"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.job_health import (
    CADENCE_TIERS, JOB_MAX_AGE, STATE_NEVER_RUN, STATE_OK, STATE_STALE, STATE_STUCK, evaluate_job_health,
)
from apps.core.models import JobRun
from apps.core.tests.test_cron_command_smoke import cron_invocations

JOB = 'send_digests'  # daily tier: 26 h window


def by_name(rows):
    return {row.job_name: row for row in rows}


class EvaluateJobHealthTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def run_row(self, status, started_ago, finished_ago=None, error='', job=JOB):
        started = self.now - started_ago
        finished = self.now - finished_ago if finished_ago is not None else None
        return JobRun.objects.create(
            job_name=job, status=status, started_at=started, finished_at=finished, error_text=error,
        )

    def health(self, job=JOB):
        return by_name(evaluate_job_health(self.now))[job]

    def test_a_job_with_no_runs_is_never_run_and_does_not_alert(self):
        row = self.health()
        self.assertEqual(row.state, STATE_NEVER_RUN)
        self.assertFalse(row.needs_alert)

    def test_recent_success_is_ok(self):
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=3), timedelta(hours=3))
        row = self.health()
        self.assertEqual(row.state, STATE_OK)
        self.assertIsNotNone(row.last_success_at)

    def test_success_older_than_the_window_is_stale(self):
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=30), timedelta(hours=30))
        row = self.health()
        self.assertEqual(row.state, STATE_STALE)
        self.assertTrue(row.needs_alert)

    def test_the_window_comes_from_the_jobs_tier(self):
        # Same 1 h old success: fresh for a daily job (26 h), stale for an every-minute job (15 min).
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=1), timedelta(hours=1), job=JOB)
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=1), timedelta(hours=1), job='drain_tasks')
        self.assertEqual(self.health(JOB).state, STATE_OK)
        self.assertEqual(self.health('drain_tasks').state, STATE_STALE)

    def test_only_failures_are_stale_and_the_error_is_surfaced(self):
        self.run_row(JobRun.STATUS_FAILED, timedelta(hours=2), timedelta(hours=2), error='AttributeError: boom')
        row = self.health()
        self.assertEqual(row.state, STATE_STALE)
        self.assertEqual(row.last_status, JobRun.STATUS_FAILED)
        self.assertIn('boom', row.last_error)

    def test_a_failed_latest_run_is_ok_while_a_success_is_still_inside_the_window(self):
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=10), timedelta(hours=10))
        self.run_row(JobRun.STATUS_FAILED, timedelta(hours=1), timedelta(hours=1), error='transient')
        row = self.health()
        self.assertEqual(row.state, STATE_OK)
        self.assertEqual(row.last_status, JobRun.STATUS_FAILED)

    def test_a_run_started_before_the_window_and_never_finished_is_stuck(self):
        self.run_row(JobRun.STATUS_RUNNING, timedelta(hours=30))
        row = self.health()
        self.assertEqual(row.state, STATE_STUCK)
        self.assertTrue(row.needs_alert)

    def test_a_young_run_in_flight_is_not_stale_even_if_the_last_success_is_old(self):
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=27), timedelta(hours=27))
        self.run_row(JobRun.STATUS_RUNNING, timedelta(minutes=5))
        self.assertEqual(self.health().state, STATE_OK)

    def test_unhealthy_jobs_sort_first(self):
        self.run_row(JobRun.STATUS_SUCCESS, timedelta(hours=2), timedelta(hours=2), job='backup_database')
        self.run_row(JobRun.STATUS_FAILED, timedelta(hours=2), timedelta(hours=2), job=JOB)
        rows = evaluate_job_health(self.now)
        self.assertEqual(rows[0].job_name, JOB)
        self.assertEqual(rows[0].state, STATE_STALE)


def scheduled_job_names():
    """The JobRun.job_name each crontab invocation writes: the command, except refresh_reporting's scope suffix."""
    names = set()
    for argv in cron_invocations():
        if argv[0] == 'refresh_reporting':
            scope = next(a.split('=', 1)[1] for a in argv if a.startswith('--scope='))
            names.add(f'refresh_reporting_{scope}')
        else:
            names.add(argv[0])
    return names


class JobWindowConfigTests(TestCase):
    def test_every_scheduled_job_has_a_freshness_window(self):
        missing = sorted(scheduled_job_names() - set(JOB_MAX_AGE))
        self.assertEqual(missing, [], f"scheduled jobs with no expected window in job_health.CADENCE_TIERS: {missing}")

    def test_no_window_for_a_job_that_is_not_scheduled(self):
        stale = sorted(set(JOB_MAX_AGE) - scheduled_job_names())
        self.assertEqual(stale, [], f"CADENCE_TIERS lists jobs that are not in deploy/crontab: {stale}")

    def test_a_job_is_in_exactly_one_tier(self):
        listed = [job for _max_age, jobs in CADENCE_TIERS.values() for job in jobs]
        duplicates = sorted({job for job in listed if listed.count(job) > 1})
        self.assertEqual(duplicates, [], f"jobs listed in more than one tier: {duplicates}")

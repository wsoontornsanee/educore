"""track_job_run: one JobRun row per scheduled run (spec/01 ARC-008)."""
from django.test import TestCase

from apps.core.job_runs import MAX_ERROR_TEXT_CHARS, track_job_run
from apps.core.models import JobRun


class TrackJobRunTests(TestCase):
    def test_success_records_counts_and_finish_time(self):
        with track_job_run('demo_job') as run:
            run.items_processed += 3
        row = JobRun.objects.get(job_name='demo_job')
        self.assertEqual(row.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(row.items_processed, 3)
        self.assertEqual(row.error_text, '')
        self.assertIsNotNone(row.finished_at)

    def test_row_is_running_while_the_block_executes(self):
        with track_job_run('demo_job'):
            self.assertEqual(JobRun.objects.get(job_name='demo_job').status, JobRun.STATUS_RUNNING)

    def test_survived_errors_end_the_run_failed_without_raising(self):
        with track_job_run('demo_job') as run:
            run.items_processed += 1
            run.add_error('school 3: boom')
            run.add_error('school 4: bang')
        row = JobRun.objects.get(job_name='demo_job')
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertEqual(row.items_processed, 1)
        self.assertEqual(row.error_text, 'school 3: boom\nschool 4: bang')

    def test_escaping_exception_is_recorded_and_reraised(self):
        with self.assertRaises(RuntimeError):
            with track_job_run('demo_job') as run:
                run.items_processed += 2
                raise RuntimeError('db went away')
        row = JobRun.objects.get(job_name='demo_job')
        self.assertEqual(row.status, JobRun.STATUS_FAILED)
        self.assertEqual(row.items_processed, 2)
        self.assertIn('RuntimeError: db went away', row.error_text)
        self.assertIsNotNone(row.finished_at)

    def test_record_false_writes_nothing_but_still_tracks(self):
        with track_job_run('demo_job', record=False) as run:
            run.items_processed += 5
            run.add_error('ignored')
        self.assertFalse(JobRun.objects.filter(job_name='demo_job').exists())

    def test_record_false_still_reraises(self):
        with self.assertRaises(ValueError):
            with track_job_run('demo_job', record=False):
                raise ValueError('x')
        self.assertFalse(JobRun.objects.filter(job_name='demo_job').exists())

    def test_error_text_is_bounded(self):
        with track_job_run('demo_job') as run:
            for i in range(5000):
                run.add_error(f'school {i}: something went wrong')
        row = JobRun.objects.get(job_name='demo_job')
        self.assertEqual(len(row.error_text), MAX_ERROR_TEXT_CHARS)

"""Tests for advisory locking and sample_cron_job (ARC-007, ARC-008)."""
from django.core.management import call_command
from django.test import TestCase
from apps.core.locks import acquire_advisory_lock, release_advisory_lock, advisory_lock
from apps.core.models import JobRun

class AdvisoryLockTests(TestCase):
    def test_lock_acquire_and_release(self):
        """Verify lock lifecycle: acquire -> reject concurrent -> release -> re-acquire."""
        lock_name = "test_job"

        # 1. Acquire
        acquired = acquire_advisory_lock(lock_name)
        self.assertTrue(acquired)

        # 2. Concurrent acquisition must fail
        concurrent = acquire_advisory_lock(lock_name)
        self.assertFalse(concurrent)

        # 3. Release
        released = release_advisory_lock(lock_name)
        self.assertTrue(released)

        # 4. Re-acquire succeeds
        reacquired = acquire_advisory_lock(lock_name)
        self.assertTrue(reacquired)
        release_advisory_lock(lock_name)

    def test_advisory_lock_context_manager(self):
        """Verify context manager releases lock cleanly on exit."""
        with advisory_lock("context_test") as acquired:
            self.assertTrue(acquired)
            # Cannot acquire inside the context
            self.assertFalse(acquire_advisory_lock("context_test"))

        # After exiting, lock is released and can be acquired again
        self.assertTrue(acquire_advisory_lock("context_test"))
        release_advisory_lock("context_test")

    def test_sample_cron_job_command(self):
        """Verify sample_cron_job runs and writes a JobRun record."""
        JobRun.objects.all().delete()

        call_command('sample_cron_job', foundation=1001)

        job_run = JobRun.objects.filter(job_name='sample_cron_job').last()
        self.assertIsNotNone(job_run)
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)
        self.assertIsNotNone(job_run.finished_at)

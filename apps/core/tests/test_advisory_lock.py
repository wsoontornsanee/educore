"""Tests for advisory locking and sample_cron_job (ARC-007, ARC-008)."""
from django.core.management import call_command
from django.db import connection as default_connection
from django.db.utils import load_backend
from django.test import TestCase
from apps.core.locks import acquire_advisory_lock, release_advisory_lock, advisory_lock
from apps.core.models import JobRun


def _second_connection():
    """A genuinely separate DB connection to the same test database, simulating
    a second process contending for the same named lock. MySQL's GET_LOCK lets
    the SAME session re-acquire a name it already holds (non-blocking, since
    MySQL 5.7.5) — Django's TestCase reuses one connection for the whole test,
    so a second acquire_advisory_lock() call from the SAME test method is, on
    MySQL, the same session re-acquiring its own lock (correctly succeeds),
    not a competing process (which would correctly be rejected). Only a real
    second connection exercises the cross-process rejection this module
    actually guarantees in production, where every cron command gets its own
    connection. On SQLite's in-memory-set fallback this changes nothing — that
    fallback is a shared process-level set regardless of which connection
    calls it, so it stays a faithful (if approximate) stand-in for either case.

    `django.db.connection` is a lazy ConnectionProxy, not the real backend
    wrapper, so its own __class__ can't be instantiated directly — this
    mirrors what ConnectionHandler does internally to build a real one.
    """
    backend = load_backend(default_connection.settings_dict['ENGINE'])
    return backend.DatabaseWrapper(default_connection.settings_dict, alias='advisory_lock_test_second')


class AdvisoryLockTests(TestCase):
    def test_lock_acquire_and_release(self):
        """Verify lock lifecycle: acquire -> reject concurrent -> release -> re-acquire."""
        lock_name = "test_job"

        # 1. Acquire
        acquired = acquire_advisory_lock(lock_name)
        self.assertTrue(acquired)

        # 2. A genuinely separate session must be rejected while the first holds the lock.
        conn2 = _second_connection()
        try:
            concurrent = acquire_advisory_lock(lock_name, db_connection=conn2)
            self.assertFalse(concurrent)
        finally:
            conn2.close()

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
            # A genuinely separate session cannot acquire it inside the context.
            conn2 = _second_connection()
            try:
                self.assertFalse(acquire_advisory_lock("context_test", db_connection=conn2))
            finally:
                conn2.close()

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

"""Tests for the expire_sessions cron command (spec/02 §4)."""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditEvent, JobRun
from apps.identity.models import OTPChallenge


class ExpireSessionsCommandTests(TestCase):
    def _make_session(self, expire_date):
        Session.objects.create(
            session_key=f"sk-{expire_date.timestamp()}",
            session_data='',
            expire_date=expire_date,
        )

    def _make_otp(self, created_at):
        otp = OTPChallenge.objects.create(
            phone_e164='+6281234567890',
            code_hash='hashed',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        OTPChallenge.objects.filter(pk=otp.pk).update(created_at=created_at)
        return otp

    def test_purges_expired_sessions_only(self):
        now = timezone.now()
        self._make_session(now - timedelta(hours=1))  # expired
        self._make_session(now + timedelta(hours=1))  # still valid

        call_command('expire_sessions', force=True)

        self.assertEqual(Session.objects.count(), 1)
        self.assertTrue(Session.objects.filter(expire_date__gt=now).exists())

    def test_purges_stale_otp_challenges_only(self):
        now = timezone.now()
        self._make_otp(now - timedelta(hours=25))  # stale
        self._make_otp(now - timedelta(hours=1))    # recent

        call_command('expire_sessions', force=True)

        self.assertEqual(OTPChallenge.objects.count(), 1)

    def test_batches_deletion_without_missing_rows(self):
        now = timezone.now()
        for i in range(5):
            self._make_session(now - timedelta(hours=1, minutes=i))

        call_command('expire_sessions', force=True, batch_size=2)

        self.assertEqual(Session.objects.count(), 0)

    def test_writes_job_run_and_audit_event(self):
        now = timezone.now()
        self._make_session(now - timedelta(hours=1))

        call_command('expire_sessions', force=True)

        job = JobRun.objects.get(job_name='expire_sessions')
        self.assertEqual(job.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job.items_processed, 1)
        self.assertTrue(AuditEvent.objects.filter(action='core.expire_sessions.sweep').exists())

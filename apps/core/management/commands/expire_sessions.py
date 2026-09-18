"""Session & short-lived auth token cleanup (spec/02 §4, deploy/crontab).

Runs hourly (deploy/crontab):
    0 * * * *      educore expire_sessions

Purges expired Django sessions and expired/consumed OTP challenges in small
batches so the delete never holds a long lock on a high-traffic auth table.

Scope note: the spec also names "blacklisted JWT tokens" and "password reset
/ magic link tokens" as purge targets. Neither exists in this codebase —
`rest_framework_simplejwt.token_blacklist` is not installed (access/refresh
tokens are stateless, nothing to blacklist), and there is no password-reset
or magic-link token model anywhere (auth is OTP-only, see apps.identity.OTPChallenge).
Documented gap, not faked: purging them is a no-op until those mechanisms exist.
"""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.core.services import audit
from apps.identity.models import OTPChallenge

OTP_RETENTION_HOURS = 24  # spec: OTP challenges older than 24 hours are purged regardless of status


def _batched_delete(base_queryset, batch_size):
    """Delete matching rows in PK chunks so no single delete locks the table for long."""
    total = 0
    while True:
        pks = list(base_queryset.values_list('pk', flat=True)[:batch_size])
        if not pks:
            break
        deleted, _ = base_queryset.model._default_manager.filter(pk__in=pks).delete()
        total += deleted
    return total


class Command(CronHostCommand):
    help = "Purge expired sessions and stale OTP challenges in safe batches (spec/02 §4)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--batch-size', type=int, default=1000,
                             help="Rows deleted per chunk (default 1000).")
        parser.add_argument('--otp-retention-hours', type=int, default=OTP_RETENTION_HOURS,
                             help=f"Purge OTP challenges older than this many hours (default {OTP_RETENTION_HOURS}).")

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        lock_name = 'expire_sessions'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name=lock_name)
            try:
                now = timezone.now()
                otp_cutoff = now - timedelta(hours=options['otp_retention_hours'])

                sessions_deleted = _batched_delete(
                    Session.objects.filter(expire_date__lt=now), batch_size,
                )
                otp_deleted = _batched_delete(
                    OTPChallenge.objects.filter(created_at__lt=otp_cutoff), batch_size,
                )
                total = sessions_deleted + otp_deleted

                audit(
                    action='core.expire_sessions.sweep',
                    entity_type='system',
                    entity_id='expire_sessions',
                    actor_id='system',
                    diff={'sessions_deleted': sessions_deleted, 'otp_challenges_deleted': otp_deleted},
                )

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = total
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"expire_sessions: purged {sessions_deleted} expired session(s), "
                    f"{otp_deleted} stale OTP challenge(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

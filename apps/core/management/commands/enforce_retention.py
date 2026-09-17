"""Retention enforcement for idempotency keys (spec/18 §4: keys retained 7 days).

Closes the pre-existing `enforce_retention` line in deploy/crontab, which had
no command behind it. Purges IdempotencyRecord rows older than the retention
window (7 days, matching the partner API's replay contract) and expired
partner webhook endpoints' soft-deleted rows. Never touches academic or
financial data (red line 2): only transient replay-cache rows are purged.
"""
from datetime import timedelta

from apps.core.management.base import CronHostCommand
from apps.core.locks import advisory_lock
from apps.core.models import IdempotencyRecord, JobRun
from django.utils import timezone

IDEMPOTENCY_RETENTION_DAYS = 7  # spec/18 §4


class Command(CronHostCommand):
    help = "Purge expired idempotency keys (7-day retention, spec/18 §4)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--retention-days',
            type=int,
            default=IDEMPOTENCY_RETENTION_DAYS,
            help=f"Days to retain idempotency records (default {IDEMPOTENCY_RETENTION_DAYS}).",
        )

    def handle(self, *args, **options):
        retention_days = options['retention_days']
        lock_name = 'enforce_retention'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name=lock_name)
            try:
                cutoff = timezone.now() - timedelta(days=retention_days)
                # IdempotencyRecord is keyed by primary key `key` — physical
                # purge is by design: these are replay-cache rows, not
                # academic/financial records, and hold no domain truth.
                deleted_count, _ = IdempotencyRecord.objects.filter(
                    created_at__lt=cutoff,
                ).delete()

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = deleted_count
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"enforce_retention: purged {deleted_count} idempotency records older than {retention_days}d."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

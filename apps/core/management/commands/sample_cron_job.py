"""Sample cron job demonstrating advisory locking and JobRun logging.

Implements:
- ARC-005: Explicit --foundation or foundation iteration
- ARC-007: MySQL named advisory lock
- ARC-008: JobRun logging
"""
import traceback
from django.utils import timezone
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun

class Command(CronHostCommand):
    help = "Demonstrates canonical cron job execution with advisory locking and JobRun logging."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--foundation',
            type=int,
            help="Optional Foundation ID to scope the job (ARC-005)."
        )

    def handle(self, *args, **options):
        foundation_id = options.get('foundation')
        lock_name = f"sample_cron_job:{foundation_id}" if foundation_id else "sample_cron_job"

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(f"Advisory lock '{lock_name}' is held by another process. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(
                job_name='sample_cron_job',
                started_at=timezone.now(),
                status=JobRun.STATUS_RUNNING,
            )

            try:
                self.stdout.write(f"Executing sample_cron_job (foundation={foundation_id})...")
                # Simulated work
                processed = 1
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = processed
                self.stdout.write(self.style.SUCCESS(f"Sample cron job finished successfully. Processed: {processed}."))

            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = traceback.format_exc()
                self.stderr.write(self.style.ERROR(f"Sample cron job failed: {exc}"))

            finally:
                job_run.finished_at = timezone.now()
                job_run.save()

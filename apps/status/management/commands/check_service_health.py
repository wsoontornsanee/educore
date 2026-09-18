"""Cron: probe platform health and roll up today's per-component status (*/5, deploy/crontab)."""
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.status.services import record_heartbeats, rollup_daily_status


class Command(CronHostCommand):
    help = "Probe platform health (DB connectivity) and roll up today's component status."

    def handle(self, *args, **options):
        with advisory_lock('check_service_health', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'check_service_health' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='check_service_health', status=JobRun.STATUS_RUNNING)
            try:
                heartbeats_created = record_heartbeats()
                components_rolled_up = rollup_daily_status()
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = heartbeats_created
                job_run.finished_at = timezone.now()
                job_run.save(update_fields=['status', 'items_processed', 'finished_at'])
                self.stdout.write(self.style.SUCCESS(
                    f"check_service_health: {heartbeats_created} heartbeats, {components_rolled_up} daily rollups"
                ))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)
                job_run.finished_at = timezone.now()
                job_run.save(update_fields=['status', 'error_text', 'finished_at'])
                raise

"""Rebuild rpt_* reporting tables (spec/15 §2, deploy/crontab */5 dashboard + nightly full).

Reporting is a read-only app: it owns the rpt_* tables and reads other apps only
through their models during a refresh, matching every other cron command's
advisory_lock + JobRun pattern.
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.reporting.services import refresh_wallet_activity

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rebuild rpt_* reporting tables (spec/15 §2)."

    def add_arguments(self, parser):
        parser.add_argument('--scope', choices=['dashboard', 'full'], required=True)

    def handle(self, *args, **options):
        scope = options['scope']
        lock_name = f'refresh_reporting_{scope}'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock 'educore:{lock_name}' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name=f'refresh_reporting_{scope}', status=JobRun.STATUS_RUNNING)

            try:
                result = refresh_wallet_activity(scope=scope)
                job_run.finished_at = timezone.now()
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = result['rows_written']
                self.stdout.write(self.style.SUCCESS(
                    f"refresh_reporting --scope={scope}: {result['rows_written']} rpt_wallet_activity row(s) refreshed since {result['start_date']}."
                ))
            except Exception as exc:
                job_run.finished_at = timezone.now()
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)
                self.stdout.write(self.style.ERROR(f"refresh_reporting --scope={scope} failed: {exc}"))
                raise
            finally:
                job_run.save()

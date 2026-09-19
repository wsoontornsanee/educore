"""Rebuild rpt_* reporting tables (spec/15 §2, deploy/crontab */5 dashboard + nightly full).

Reporting is a read-only app: it owns the rpt_* tables and reads other apps only
through their models during a refresh, matching every other cron command's
advisory_lock + JobRun pattern.
"""
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.reporting.services import (
    refresh_academic_performance,
    refresh_active_students,
    refresh_ar_aging,
    refresh_daily_attendance,
    refresh_daily_finance,
    refresh_foundation_kpis,
    refresh_subscription_charges,
    refresh_wallet_activity,
)

logger = logging.getLogger(__name__)

REFRESHERS = [
    ('rpt_wallet_activity', refresh_wallet_activity),
    ('rpt_daily_attendance', refresh_daily_attendance),
    ('rpt_academic_performance', refresh_academic_performance),
    ('rpt_active_students', refresh_active_students),
    ('rpt_subscription_charges', refresh_subscription_charges),
    ('rpt_daily_finance', refresh_daily_finance),
    ('rpt_ar_aging', refresh_ar_aging),
    ('rpt_foundation_kpis', refresh_foundation_kpis),
]


class Command(CronHostCommand):
    help = "Rebuild rpt_* reporting tables (spec/15 §2)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
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
                total_rows = 0
                summary = []
                for table_name, refresher in REFRESHERS:
                    result = refresher(scope=scope)
                    total_rows += result['rows_written']
                    summary.append(f"{result['rows_written']} {table_name}")

                job_run.finished_at = timezone.now()
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = total_rows
                self.stdout.write(self.style.SUCCESS(
                    f"refresh_reporting --scope={scope}: {', '.join(summary)} row(s) refreshed."
                ))
            except Exception as exc:
                job_run.finished_at = timezone.now()
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)
                self.stdout.write(self.style.ERROR(f"refresh_reporting --scope={scope} failed: {exc}"))
                raise
            finally:
                job_run.save()

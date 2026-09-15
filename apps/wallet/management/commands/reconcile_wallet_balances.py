"""Nightly wallet balance integrity check (spec/07 §3, WAL-002, deploy/crontab 19:00).

Recomputes every wallet's balance from its transaction log and reports mismatches
as incidents (never silently auto-corrected).
"""
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.wallet.services import reconcile_wallet_balances

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Recompute every wallet balance from its transaction log and report drift (WAL-002)."

    def handle(self, *args, **options):
        with advisory_lock('reconcile_wallet_balances', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:reconcile_wallet_balances' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='reconcile_wallet_balances', status=JobRun.STATUS_RUNNING)

            try:
                report = reconcile_wallet_balances()
                job_run.finished_at = timezone.now()
                job_run.items_processed = report['checked']
                if report['mismatches']:
                    job_run.status = JobRun.STATUS_FAILED
                    job_run.error_text = f"{len(report['mismatches'])} wallet(s) with balance drift: {report['mismatches']}"
                    self.stdout.write(self.style.ERROR(job_run.error_text))
                else:
                    job_run.status = JobRun.STATUS_SUCCESS
                    self.stdout.write(self.style.SUCCESS(f"Checked {report['checked']} wallets, zero drift."))
                job_run.save()
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

"""Wallet auto-top-up trigger (spec/07 §3, WAL-005, WAL-006).

Generates a new VA/QRIS WalletTopupIntent for every wallet whose balance has
dropped below its guardian-configured threshold — the guardian still
completes the transfer themselves (semi-automatic, not a silent auto-debit;
neither VA nor QRIS has a rail for that).
"""
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.wallet.services import process_wallet_auto_topups

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Trigger auto-top-up VA/QRIS intents for wallets below their configured threshold (WAL-005, WAL-006)."

    def handle(self, *args, **options):
        with advisory_lock('process_wallet_auto_topups', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:process_wallet_auto_topups' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='process_wallet_auto_topups', status=JobRun.STATUS_RUNNING)

            try:
                result = process_wallet_auto_topups()
                job_run.finished_at = timezone.now()
                job_run.items_processed = result['triggered']
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"Triggered {result['triggered']} auto-top-up(s), "
                    f"skipped {result['skipped_pending']} with a pending intent already."
                ))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

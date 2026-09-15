"""48h reminder for open wallet reconciliation cases (spec/17 §8, REC-012, deploy/crontab */30).

Sends exactly one reminder per wallet, only for cases whose initial notice was
actually delivered (REC-029) — the clock measures the guardian's time to act.
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.wallet.services import get_wallets_due_for_reminder, queue_reconciliation_reminder

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sends the single 48h reminder for open wallet reconciliation cases (REC-012)."

    def handle(self, *args, **options):
        with advisory_lock('send_wallet_recon_reminders', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:send_wallet_recon_reminders' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='send_wallet_recon_reminders', status=JobRun.STATUS_RUNNING)

            try:
                wallets = list(get_wallets_due_for_reminder())
                reminded = 0
                for wallet in wallets:
                    if queue_reconciliation_reminder(wallet):
                        reminded += 1

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = reminded
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(f"Sent reminders for {reminded} wallet(s)."))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(e)
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

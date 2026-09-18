"""Management command: poll pending payment gateway intents (spec/01 §5.1).

Runs every 5 minutes (deploy/crontab):
    */5 * * * * python /app/manage.py sync_payment_status

Safety net for a FIN-013 settlement webhook that never arrived — the webhook
stays the primary settlement path; this only catches what it missed, and
expires/cancels intents the gateway can no longer settle.
"""
import logging

from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.finance.services.payments import sync_payment_status

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Poll pending gateway payment intents for a missed FIN-013 settlement webhook (ARC-006)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help="Maximum number of PENDING payment intents to check this run (ARC-009, default: 50).",
        )
        parser.add_argument(
            '--max-age-hours',
            type=int,
            default=24,
            help="Skip intents created more than this many hours ago (default: 24).",
        )
        parser.add_argument(
            '--provider',
            type=str,
            default=None,
            help="Poll only this provider (e.g. XENDIT, MIDTRANS). Defaults to all providers.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Poll gateways and report what would happen without writing to the DB.",
        )

    def handle(self, *args, **options):
        with advisory_lock('sync_payment_status', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:sync_payment_status' already held. Exiting."
                ))
                return

            dry_run = options.get('dry_run', False)
            job_run = JobRun.objects.create(job_name='sync_payment_status', status=JobRun.STATUS_RUNNING)

            try:
                result = sync_payment_status(
                    limit=options['batch_size'],
                    max_age_hours=options['max_age_hours'],
                    provider_name=options.get('provider'),
                    dry_run=dry_run,
                )
                job_run.finished_at = timezone.now()
                job_run.items_processed = result['checked']
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()
                dry_tag = " [DRY RUN]" if dry_run else ""
                self.stdout.write(self.style.SUCCESS(
                    f"Checked {result['checked']} pending intent(s){dry_tag}: "
                    f"settled={result['settled']} expired={result['expired']} "
                    f"cancelled={result['cancelled']} errors={result['errors']}"
                ))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

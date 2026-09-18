"""Django management command aggregating held notifications into one evening digest per
recipient (spec/13 §3 NTF-007, deploy/crontab:20).

Enforces:
- Advisory locking with educore:send_digests (ARC-007, spec/01 §5).
- Cron host gating via CronHostCommand (ARC-013, spec/01 §7).
- Scheduled execution tracking via JobRun (ARC-008).
- Multi-tenancy boundary via with tenant_context(foundation_id).
- Only categories flagged digest_only in CATEGORY_CONFIG are aggregated; HIGH/CRITICAL
  categories are never held (send_due_notifications dispatches them within its own tick).
"""
import datetime
import logging
import traceback

from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.identity.models import Foundation
from apps.notifications.services import run_daily_digest
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Aggregates digest_only-category notifications into one daily message per recipient (spec/13 §3, NTF-007)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--date',
            type=str,
            help="Target calendar date in YYYY-MM-DD format (defaults to today).",
        )
        parser.add_argument(
            '--foundation-id',
            type=int,
            help="Restrict the run to a single foundation (defaults to all active foundations).",
        )
        parser.add_argument(
            '--channel',
            type=str,
            choices=['whatsapp', 'email'],
            help="Restrict digest delivery to a single channel (defaults to category/preference resolution).",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Compute groupings and counts without dispatching or mutating any records.",
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        foundation_id = options.get('foundation_id')
        channel = options.get('channel')
        dry_run = options.get('dry_run')

        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid --date '{date_str}'. Expected YYYY-MM-DD."))
                return
            as_of = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time(17, 0)))
        else:
            as_of = timezone.now()

        lock_name = 'educore:send_digests'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(f"Advisory lock '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(
                job_name='send_digests',
                started_at=timezone.now(),
                status=JobRun.STATUS_RUNNING,
            )

            items_digested = 0
            digests_sent = 0
            has_error = False
            last_error_text = ''

            try:
                foundations_qs = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)
                if foundation_id:
                    foundations_qs = foundations_qs.filter(id=foundation_id)

                for foundation in foundations_qs:
                    with tenant_context(foundation.id):
                        result = run_daily_digest(
                            foundation_id=foundation.id,
                            as_of=as_of,
                            channel=channel,
                            dry_run=dry_run,
                        )
                        items_digested += result['items_digested']
                        digests_sent += result['digests_sent']

                job_run.status = JobRun.STATUS_SUCCESS
                verb = "Would digest" if dry_run else "Digested"
                self.stdout.write(self.style.SUCCESS(
                    f"{verb} {items_digested} item(s) into {digests_sent} digest message(s)."
                ))

            except Exception as exc:
                has_error = True
                last_error_text = traceback.format_exc()
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = last_error_text
                self.stderr.write(self.style.ERROR(f"Error sending digests: {exc}"))

            finally:
                job_run.finished_at = timezone.now()
                job_run.items_processed = items_digested
                if has_error and not job_run.error_text:
                    job_run.error_text = last_error_text
                job_run.save()

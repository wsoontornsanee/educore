"""Django management command to execute the automated arrears reminder ladder (spec/06 §6, deploy/crontab:20).

Enforces:
- Advisory locking with educore:run_arrears_ladder (ARC-007, spec/06 §9.9).
- Cron host gating via CronHostCommand (ARC-013, spec/01 §7).
- Configurable reminder ladder offsets per school: T-3, T-0, T+3, T+7, T+14, T+30 (FIN-026).
- Send-time cancellation for settled invoices (FIN-027, NTF-004).
- WhatsApp -> Push -> SMS fallback channel priority (FIN-028).
"""
import datetime
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.job_runs import track_job_run
from apps.core.management.base import CronHostCommand
from apps.finance.services.arrears import run_arrears_ladder
from apps.identity.models import Foundation, School
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Run the automated tuition arrears reminder ladder and escalation (spec/06 §6, FIN-026)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--as-of-date',
            type=str,
            help="Reference date for ladder evaluation in YYYY-MM-DD format (defaults to today).",
        )
        parser.add_argument(
            '--school-id',
            type=int,
            help="Execute reminder ladder only for a specific school ID.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview eligible invoices and reminders without queueing notification intents.",
        )

    def handle(self, *args, **options):
        # 1. Acquire named advisory lock (ARC-007)
        with advisory_lock('run_arrears_ladder', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:run_arrears_ladder' already held. Exiting immediately."
                ))
                return

            as_of_date_str = options.get('as_of_date')
            if as_of_date_str:
                try:
                    as_of_date = datetime.date.fromisoformat(as_of_date_str)
                except ValueError:
                    self.stdout.write(self.style.ERROR(
                        f"Invalid date format: {as_of_date_str}. Expected YYYY-MM-DD."
                    ))
                    return
            else:
                as_of_date = timezone.localdate()

            dry_run = options.get('dry_run', False)
            school_id = options.get('school_id')

            self.stdout.write(
                f"Starting arrears reminder ladder execution (as_of_date={as_of_date}, dry_run={dry_run})"
            )

            with track_job_run('run_arrears_ladder', record=not dry_run) as run:
                foundations = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)

                total_evaluated = 0
                total_eligible = 0
                total_dispatched = 0
                total_skipped = 0

                for foundation in foundations:
                    with tenant_context(foundation.id):
                        schools_qs = School.objects.filter(foundation_id=foundation.id, is_active=True)
                        if school_id:
                            schools_qs = schools_qs.filter(id=school_id)

                        for school in schools_qs:
                            res = run_arrears_ladder(
                                school=school,
                                as_of_date=as_of_date,
                                dry_run=dry_run,
                            )

                            evaluated = res['evaluated_count']
                            eligible = res['eligible_count']
                            dispatched = res['dispatched_count']
                            skipped = res['skipped_existing_count']

                            total_evaluated += evaluated
                            total_eligible += eligible
                            total_dispatched += dispatched
                            run.items_processed += dispatched
                            total_skipped += skipped

                            status_note = " (dry run)" if dry_run else ""
                            self.stdout.write(
                                f"  [{school.name}] Evaluated: {evaluated}, Eligible: {eligible}, "
                                f"Dispatched: {dispatched}, Skipped: {skipped}{status_note}"
                            )

            self.stdout.write(self.style.SUCCESS(
                f"Arrears ladder completed. Evaluated: {total_evaluated}, Eligible: {total_eligible}, "
                f"Dispatched: {total_dispatched}, Skipped (existing): {total_skipped}"
            ))

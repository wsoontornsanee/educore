"""Django management command to execute automated weekly merchant settlement (spec/07 §6 WAL-022, deploy/crontab:30).

Enforces:
- Advisory locking with educore:settle_merchants (ARC-007, spec/01 §5).
- Cron host gating via CronHostCommand (ARC-013, spec/01 §7).
- Scheduled execution tracking via JobRun (ARC-008).
- Multi-tenancy boundary via with tenant_context(foundation_id).
- School timezone awareness via school.timezone and with timezone.override (ARC-014).
- Idempotent re-runs updating PENDING settlements in place (WAL-022).
- Statement generation via generate_settlement_statement_pdf (ARC-026, ARC-030).
"""
import datetime
import logging
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.identity.models import Foundation, School
from apps.wallet.services import settle_merchants_for_school
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Run automated weekly merchant settlement and generate statement PDFs (spec/07 §6 WAL-022, deploy/crontab:30)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--date',
            '--anchor-date',
            dest='anchor_date',
            type=str,
            help="Anchor calendar date in YYYY-MM-DD format (defaults to current date in school timezone).",
        )
        parser.add_argument(
            '--period-start',
            type=str,
            help="Explicit settlement period start date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            '--period-end',
            type=str,
            help="Explicit settlement period end date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help="Settlement period duration in days when period-start is not explicitly specified (default: 7).",
        )
        parser.add_argument(
            '--school-id',
            type=str,
            help="Execute settlement only for a specific school ID.",
        )
        parser.add_argument(
            '--merchant-id',
            type=str,
            help="Execute settlement only for a specific merchant ID.",
        )
        parser.add_argument(
            '--foundation-id',
            type=str,
            help="Execute settlement only for a specific foundation ID.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview eligible settlements, transaction counts, and financial totals without modifying the database or generating statements.",
        )
        parser.add_argument(
            '--skip-zero-sales',
            action='store_true',
            help="Skip creating or updating settlements for merchants with zero completed transactions in the period.",
        )
        parser.add_argument(
            '--no-statements',
            action='store_true',
            help="Skip generating settlement statement PDF/HTML files.",
        )

    def handle(self, *args, **options):
        lock_name = 'settle_merchants'

        # 1. Acquire named advisory lock (ARC-007)
        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock 'educore:{lock_name}' already held. Exiting immediately."
                ))
                return

            anchor_str = options.get('anchor_date')
            start_str = options.get('period_start')
            end_str = options.get('period_end')

            anchor_date: Optional[datetime.date] = None
            period_start: Optional[datetime.date] = None
            period_end: Optional[datetime.date] = None

            try:
                if anchor_str:
                    anchor_date = datetime.date.fromisoformat(anchor_str)
                if start_str:
                    period_start = datetime.date.fromisoformat(start_str)
                if end_str:
                    period_end = datetime.date.fromisoformat(end_str)
            except ValueError as e:
                self.stdout.write(self.style.ERROR(f"Invalid date format: {e}. Expected YYYY-MM-DD."))
                return

            if period_start and period_end and period_start > period_end:
                self.stdout.write(self.style.ERROR(
                    f"Invalid period: start ({period_start}) cannot be after end ({period_end})."
                ))
                return

            days = options.get('days', 7)
            dry_run = options.get('dry_run', False)
            skip_zero_sales = options.get('skip_zero_sales', False)
            generate_statements = not options.get('no_statements', False)
            school_id = options.get('school_id')
            merchant_id = options.get('merchant_id')
            foundation_id = options.get('foundation_id')

            # 2. Initialize JobRun tracking (ARC-008)
            job_run = None
            if not dry_run:
                job_run = JobRun.objects.create(
                    job_name=lock_name,
                    status=JobRun.STATUS_RUNNING,
                    started_at=timezone.now(),
                )

            self.stdout.write(
                f"Starting merchant settlement sweep (start={period_start or 'auto'}, "
                f"end={period_end or 'auto'}, anchor={anchor_date or 'today_local'}, "
                f"days={days}, dry_run={dry_run}, skip_zero_sales={skip_zero_sales})"
            )

            # 3. Outer tenant orchestration
            foundations_qs = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)
            if foundation_id:
                foundations_qs = foundations_qs.filter(id=foundation_id)

            total_settled = 0
            total_skipped = 0
            total_failed = 0
            total_gross = Decimal('0.00')
            total_commission = Decimal('0.00')
            total_net = Decimal('0.00')
            error_messages = []

            for foundation in foundations_qs:
                with tenant_context(foundation.id):
                    schools_qs = School.objects.filter(foundation_id=foundation.id, is_active=True)
                    if school_id:
                        schools_qs = schools_qs.filter(id=school_id)

                    for school in schools_qs:
                        try:
                            res = settle_merchants_for_school(
                                school=school,
                                period_start=period_start,
                                period_end=period_end,
                                anchor_date=anchor_date,
                                days=days,
                                dry_run=dry_run,
                                skip_zero_sales=skip_zero_sales,
                                generate_statements=generate_statements,
                                merchant_id=merchant_id,
                            )
                            total_settled += res.get('settled', 0)
                            total_skipped += res.get('skipped', 0)
                            total_failed += res.get('failed', 0)
                            total_gross += res.get('total_gross', Decimal('0.00'))
                            total_commission += res.get('total_commission', Decimal('0.00'))
                            total_net += res.get('total_net', Decimal('0.00'))

                            self.stdout.write(
                                f"  [{school.name}] Period: {res['period_start']}..{res['period_end']} | "
                                f"Settled: {res['settled']}, Skipped: {res['skipped']}, Failed: {res['failed']} | "
                                f"Gross: Rp {res['total_gross']}, Net: Rp {res['total_net']}"
                            )

                            for r in res.get('results', []):
                                if r.get('status') == 'FAILED':
                                    err_detail = f"Merchant #{r.get('merchant_id')} ({r.get('merchant_name')}): {r.get('error')}"
                                    error_messages.append(err_detail)
                                    self.stdout.write(self.style.ERROR(f"    - {err_detail}"))
                                elif r.get('status') in ('SKIPPED_ALREADY_PAID', 'SKIPPED_ZERO_SALES'):
                                    self.stdout.write(f"    - {r.get('merchant_name')}: {r.get('status')} ({r.get('reason')})")

                        except Exception as exc:
                            err_msg = f"Error processing school #{school.id} ({school.name}): {exc}"
                            logger.exception(err_msg)
                            error_messages.append(err_msg)
                            self.stdout.write(self.style.ERROR(err_msg))

            self.stdout.write(
                f"Merchant settlement sweep complete: {total_settled} settled, "
                f"{total_skipped} skipped, {total_failed} failed. "
                f"Total Gross: Rp {total_gross}, Net: Rp {total_net}"
            )

            # 4. Finalize JobRun record (ARC-008)
            if job_run:
                job_run.finished_at = timezone.now()
                job_run.items_processed = total_settled
                if error_messages or total_failed > 0:
                    job_run.status = JobRun.STATUS_FAILED
                    job_run.error_text = "\n".join(error_messages)
                else:
                    job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()

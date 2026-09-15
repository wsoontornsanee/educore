"""Django management command to generate monthly SPP and tuition invoices (spec/06 §3, deploy/crontab:19).

Enforces:
- Advisory locking with educore:generate_invoices (ARC-007, spec/06 §9.9).
- Idempotency per (student, period) (FIN-003).
- Gapless sequential invoice numbers (FIN-004).
- Exclusion of non-ACTIVE students (FIN-005).
- Rounding PEMBULATAN line items to nearest Rp 100 for IDR (FIN-008c, CUR-019).
"""
import datetime
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.identity.models import Foundation, School
from apps.finance.services import generate_monthly_invoices
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Generate monthly student tuition and SPP invoices (spec/06 §3, FIN-002)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--period',
            type=str,
            help="Billing period in YYYY-MM format (e.g. 2026-11).",
        )
        parser.add_argument(
            '--next-period',
            action='store_true',
            help="Automatically targets the following month (standard 25th cron run per FIN-002).",
        )
        parser.add_argument(
            '--school-id',
            type=int,
            help="Generate only for a specific school ID.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview generation counts and totals without persisting invoices.",
        )

    def handle(self, *args, **options):
        # 1. Acquire advisory lock (ARC-007, spec/06 §9.9)
        with advisory_lock('generate_invoices', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:generate_invoices' already held. Exiting immediately."
                ))
                return

            period = options.get('period')
            if options.get('next-period') or not period:
                # Compute next month
                today = timezone.localdate()
                year = today.year + (1 if today.month == 12 else 0)
                month = 1 if today.month == 12 else today.month + 1
                period = f"{year:04d}-{month:02d}"

            dry_run = options.get('dry_run', False)
            school_id = options.get('school_id')

            self.stdout.write(f"Starting invoice generation for period: {period} (dry_run={dry_run})")

            foundations = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)

            total_generated = 0
            total_skipped = 0

            for foundation in foundations:
                with tenant_context(foundation.id):
                    schools_qs = School.objects.filter(foundation_id=foundation.id, is_active=True)
                    if school_id:
                        schools_qs = schools_qs.filter(id=school_id)

                    for school in schools_qs:
                        self.stdout.write(f"Processing school: {school.name} (NPSN: {school.npsn})...")
                        res = generate_monthly_invoices(
                            school=school,
                            period=period,
                            dry_run=dry_run,
                            triggered_by=None,
                        )

                        gen_count = res['generated_count']
                        skip_count = res['skipped_existing_count']
                        total_generated += gen_count
                        total_skipped += skip_count

                        self.stdout.write(
                            f"  - Generated: {gen_count}, Skipped (existing): {skip_count}, "
                            f"Net Billed: {res['currency']} {res['total_net_amount']}"
                        )

            self.stdout.write(self.style.SUCCESS(
                f"Invoice generation completed. Total Generated: {total_generated}, Skipped: {total_skipped}"
            ))

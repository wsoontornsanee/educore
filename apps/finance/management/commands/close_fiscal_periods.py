"""Django management command to close monthly fiscal periods and lock ledger entries (spec/06 §5, FIN-025, FIN-025b).

Enforces:
- Advisory locking with educore:close_fiscal_periods (ARC-007, spec/06 §9.9).
- Cron host gating via CronHostCommand (ARC-013, spec/01 §7).
- Period close double-entry validation (total_debit == total_credit).
- Multi-tenancy isolation via tenant_context.
- Optional --dry-run preview.
"""
import datetime
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.job_runs import track_job_run
from apps.core.management.base import CronHostCommand
from apps.identity.models import Foundation, School
from apps.finance.models import FiscalPeriod, FiscalPeriodStatus
from apps.finance.services.period_close import (
    PeriodCloseValidationError,
    close_fiscal_period,
    get_period_date_range,
    validate_period_format,
)
from apps.finance.services.ledger import LedgerEntry
from django.db.models import Sum
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Close monthly fiscal periods and lock ledgers against backdated postings (spec/06 §5, FIN-025, FIN-025b)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--period',
            type=str,
            default=None,
            help="Fiscal period to close in YYYY-MM format (defaults to previous calendar month).",
        )
        parser.add_argument(
            '--school-id',
            type=int,
            default=None,
            help="Close fiscal period only for a specific school ID.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview ledger balances and period close readiness without closing.",
        )

    def handle(self, *args, **options):
        with advisory_lock('close_fiscal_periods', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:close_fiscal_periods' already held. Exiting immediately."
                ))
                return

            period = options.get('period')
            if not period:
                # Default to previous month
                today = timezone.localdate()
                first_of_this_month = today.replace(day=1)
                last_of_prev_month = first_of_this_month - datetime.timedelta(days=1)
                period = last_of_prev_month.strftime('%Y-%m')

            try:
                validate_period_format(period)
            except ValueError as e:
                self.stdout.write(self.style.ERROR(f"Format periode tidak valid: {e}"))
                return

            dry_run = options.get('dry_run', False)
            school_id = options.get('school_id')

            self.stdout.write(
                f"Memulai proses penutupan periode fiskal {period} (dry_run={dry_run})"
            )

            with track_job_run('close_fiscal_periods', record=not dry_run) as run:
                foundations = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)
                total_closed = 0
                total_already_closed = 0
                total_failed = 0

                for foundation in foundations:
                    with tenant_context(foundation.id):
                        schools_qs = School.objects.filter(foundation_id=foundation.id, is_active=True)
                        if school_id:
                            schools_qs = schools_qs.filter(id=school_id)

                        for school in schools_qs:
                            # Check if already closed
                            existing = FiscalPeriod.all_tenants.filter(
                                foundation_id=foundation.id,
                                school=school,
                                period=period,
                                deleted_at__isnull=True,
                            ).first()

                            if existing and existing.status == FiscalPeriodStatus.CLOSED:
                                self.stdout.write(
                                    f"  [{school.name}] Periode {period} sudah ditutup sebelumnya."
                                )
                                total_already_closed += 1
                                continue

                            if dry_run:
                                start_date, end_date = get_period_date_range(period)
                                start_dt = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min))
                                end_dt = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max))
                                entries_qs = LedgerEntry.all_tenants.filter(
                                    foundation_id=foundation.id,
                                    school=school,
                                    occurred_at__gte=start_dt,
                                    occurred_at__lte=end_dt,
                                    deleted_at__isnull=True,
                                )
                                aggregates = entries_qs.aggregate(
                                    total_dr=Sum('debit'),
                                    total_cr=Sum('credit'),
                                )
                                dr = aggregates['total_dr'] or 0
                                cr = aggregates['total_cr'] or 0
                                balanced = (dr == cr)
                                self.stdout.write(
                                    f"  [{school.name}] [DRY-RUN] Periode {period}: Total Debit={dr}, Total Kredit={cr}, "
                                    f"Balanced={balanced}"
                                )
                            else:
                                try:
                                    close_fiscal_period(
                                        school=school,
                                        period=period,
                                        closed_by=None,
                                        notes="Penutupan otomatis akhir periode via cron.",
                                    )
                                    self.stdout.write(self.style.SUCCESS(
                                        f"  [{school.name}] Periode {period} berhasil ditutup."
                                    ))
                                    total_closed += 1
                                    run.items_processed += 1
                                except (PeriodCloseValidationError, ValueError) as exc:
                                    self.stdout.write(self.style.ERROR(
                                        f"  [{school.name}] Gagal menutup periode {period}: {exc}"
                                    ))
                                    total_failed += 1
                                    run.add_error(f"{school.name} {period}: {exc}")

            self.stdout.write(self.style.SUCCESS(
                f"Selesai. Ditutup: {total_closed}, Sudah ditutup: {total_already_closed}, Gagal: {total_failed}."
            ))

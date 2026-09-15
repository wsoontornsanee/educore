"""Management command: reconcile daily payment gateway settlements (spec/06 FIN-024).

Runs at 18:00 WIB daily (deploy/crontab):
    0 18 * * * python /app/manage.py reconcile_payments --date=today

Enforces:
- Advisory locking (educore:reconcile_payments) prevents overlapping runs.
- Cron host gating via CronHostCommand (EDUCORE_CRON_HOST=1 required).
- Runs per-foundation in tenant_context, iterating all active providers.
- --dry-run mode: logs what would happen without writing to the DB.
"""
import datetime
import logging

from django.conf import settings
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.finance.services.reconciliation import reconcile_gateway_settlement
from apps.identity.models import Foundation
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)

DEFAULT_PROVIDERS = ['XENDIT', 'MIDTRANS']


class Command(CronHostCommand):
    help = (
        "Fetch and reconcile daily gateway settlement reports against "
        "EduCore payment records (spec/06 FIN-024)."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--date',
            type=str,
            default='today',
            help=(
                "Settlement date to reconcile in YYYY-MM-DD format, "
                "or 'today' / 'yesterday' (default: today)."
            ),
        )
        parser.add_argument(
            '--provider',
            type=str,
            default=None,
            help=(
                "Reconcile only this provider (e.g. XENDIT). "
                "Defaults to all configured providers."
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview reconciliation without writing any DB changes.",
        )

    def handle(self, *args, **options):
        with advisory_lock('reconcile_payments', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:reconcile_payments' already held. "
                    "Another reconciliation run is active. Exiting."
                ))
                return

            settlement_date = self._resolve_date(options['date'])
            if settlement_date is None:
                return

            dry_run = options.get('dry_run', False)
            provider_arg = options.get('provider')
            providers = [provider_arg.upper()] if provider_arg else self._configured_providers()

            self.stdout.write(
                f"Starting reconciliation: date={settlement_date}, "
                f"providers={providers}, dry_run={dry_run}"
            )

            foundations = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)
            grand_total = grand_matched = grand_missing = grand_mismatch = 0

            for foundation in foundations:
                with tenant_context(foundation.id):
                    for provider_name in providers:
                        result = reconcile_gateway_settlement(
                            provider_name=provider_name,
                            settlement_date=settlement_date,
                            foundation_id=foundation.id,
                            dry_run=dry_run,
                        )
                        dry_tag = " [DRY RUN]" if dry_run else ""
                        error_tag = f" ERROR: {result.get('error', '')}" if 'error' in result else ""
                        self.stdout.write(
                            f"  [{foundation.name}] {provider_name}{dry_tag}: "
                            f"total={result['total']} matched={result['matched']} "
                            f"missing={result['missing']} mismatch={result['mismatch']}"
                            f"{error_tag}"
                        )
                        grand_total += result['total']
                        grand_matched += result['matched']
                        grand_missing += result['missing']
                        grand_mismatch += result['mismatch']

            style = self.style.SUCCESS if grand_missing == 0 and grand_mismatch == 0 else self.style.WARNING
            self.stdout.write(style(
                f"Reconciliation complete. "
                f"Total: {grand_total}, Matched: {grand_matched}, "
                f"Missing: {grand_missing}, Mismatched: {grand_mismatch}"
            ))

    def _resolve_date(self, date_str: str) -> datetime.date | None:
        """Parse the --date argument. Returns None and writes an error on bad input."""
        if date_str == 'today':
            return timezone.localdate()
        if date_str == 'yesterday':
            return timezone.localdate() - datetime.timedelta(days=1)
        try:
            return datetime.date.fromisoformat(date_str)
        except ValueError:
            self.stdout.write(self.style.ERROR(
                f"Invalid date '{date_str}'. Use YYYY-MM-DD, 'today', or 'yesterday'."
            ))
            return None

    def _configured_providers(self) -> list[str]:
        """Return providers from settings, falling back to defaults."""
        return getattr(settings, 'PAYMENT_PROVIDERS', DEFAULT_PROVIDERS)

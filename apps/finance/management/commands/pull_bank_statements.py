"""Management command: automated SFTP pull of daily bank statement files
(spec/14 CMP-024, CMP-026).

Runs daily (deploy/crontab):
    0 5 * * *   python /app/manage.py pull_bank_statements --date=yesterday

fetch_bank_statement_via_sftp is a deliberate stub (see
apps.finance.services.bank_sftp_pull's module docstring) — every configured
bank will fail with a clear BankSftpPullError until a real SFTP client is
implemented. This command exists now so the cron schedule, advisory
locking, and per-config failure tracking (visible as FAILED
GatewaySettlementBatch rows) are already in place for that follow-up —
implementing the real client later needs no changes here beyond swapping
fetch_bank_statement_via_sftp's body.

Enforces:
- Advisory locking (educore:pull_bank_statements) prevents overlapping runs.
- Cron host gating via CronHostCommand (EDUCORE_CRON_HOST=1 required).
- One config's failure never stops the others from being attempted.
"""
import datetime
import logging

from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.job_runs import track_job_run
from apps.core.management.base import CronHostCommand
from apps.finance.models import BankSftpConfig
from apps.finance.services.bank_sftp_pull import BankSftpPullError, fetch_bank_statement_via_sftp
from apps.finance.services.reconciliation import record_bank_sftp_pull_failure

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Pull daily bank statement files via SFTP for reconciliation (spec/14 CMP-024, CMP-026)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--date', type=str, default='yesterday',
            help="Settlement date to pull in YYYY-MM-DD format, or 'today'/'yesterday' (default: yesterday).",
        )
        parser.add_argument(
            '--school-id', type=int, default=None,
            help="Only pull configs for this school. Defaults to all active configs.",
        )

    def handle(self, *args, **options):
        with advisory_lock('pull_bank_statements', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:pull_bank_statements' already held. Exiting."
                ))
                return

            settlement_date = self._resolve_date(options['date'])
            if settlement_date is None:
                return

            configs = BankSftpConfig.all_tenants.filter(is_active=True, deleted_at__isnull=True)
            school_id = options.get('school_id')
            if school_id:
                configs = configs.filter(school_id=school_id)

            self.stdout.write(f"Pulling bank statements: date={settlement_date}, configs={configs.count()}")

            with track_job_run('pull_bank_statements') as run:
                succeeded = 0
                failed = 0
                for config in configs:
                    try:
                        fetch_bank_statement_via_sftp(config, settlement_date)
                    except BankSftpPullError as exc:
                        record_bank_sftp_pull_failure(
                            bank_code=config.bank_code,
                            settlement_date=settlement_date,
                            foundation_id=config.foundation_id,
                            error=str(exc),
                        )
                        self.stdout.write(self.style.ERROR(
                            f"  [school={config.school_id}] {config.bank_code}: FAILED - {exc}"
                        ))
                        failed += 1
                        run.add_error(f"school={config.school_id} {config.bank_code}: {exc}")
                        continue

                    succeeded += 1  # pragma: no cover - unreachable until a real client exists
                    run.items_processed += 1  # pragma: no cover

            self.stdout.write(self.style.WARNING(
                f"Bank SFTP pull complete. Succeeded: {succeeded}, Failed: {failed}."
            ))

    def _resolve_date(self, date_str: str) -> datetime.date | None:
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

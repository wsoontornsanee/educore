"""Keep the hot set of the high-write tables small (spec/01 NFR-009).

Moves rows older than each table's retention window into its `<table>_archive` sibling
in bounded batches (ARC-009). Nothing is dropped: financial and audit rows are archived,
never deleted (AGENTS red line 2). See apps.core.archiving for the per-table policy.
"""
from django.utils import timezone

from apps.core.archiving import POLICIES, archive_batch, eligible_rows
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun

JOB_NAME = 'archive_high_write_tables'


class Command(CronHostCommand):
    help = "Archive rows past retention from gate_events, wallet/pos transactions, audit and domain events (NFR-009)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--table', action='append', choices=[p.source._meta.db_table for p in POLICIES],
            help="Only archive this table (repeatable). Default: all five.",
        )
        parser.add_argument(
            '--retention-days', type=int,
            help="Override every selected table's retention window (default: per-table policy).",
        )
        parser.add_argument(
            '--limit', type=int, default=50000,
            help="Max rows archived per table per run, so a cron slot stays bounded (ARC-009).",
        )
        parser.add_argument(
            '--batch-size', type=int, default=1000,
            help="Rows moved per transaction.",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Count what would be archived without moving anything.",
        )

    def handle(self, *args, **options):
        policies = [p for p in POLICIES if not options['table'] or p.source._meta.db_table in options['table']]
        retention_days, limit, batch_size = options['retention_days'], options['limit'], options['batch_size']

        with advisory_lock(JOB_NAME, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{JOB_NAME}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name=JOB_NAME)
            try:
                total = 0
                for policy in policies:
                    table = policy.source._meta.db_table
                    if options['dry_run']:
                        count = min(eligible_rows(policy, retention_days).count(), limit)
                        self.stdout.write(f"{table}: {count} row(s) would be archived (dry-run).")
                        continue
                    moved = 0
                    while moved < limit:
                        batch = archive_batch(policy, min(batch_size, limit - moved), retention_days)
                        if not batch:
                            break
                        moved += batch
                    total += moved
                    self.stdout.write(f"{table}: archived {moved} row(s).")

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = total
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(f"{JOB_NAME}: archived {total} row(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

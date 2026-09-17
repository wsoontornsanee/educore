"""Sync all CONNECTED calendar connections (spec/14 §6 calendar sync).

Every scheduled-command convention applies: CronHostCommand (ARC-013),
advisory lock `educore:sync_calendars` (ARC-007), JobRun logging (ARC-008).
One connection's provider failure never aborts the sweep — it marks the
connection ERROR/REVOKED and moves on.
"""
from apps.calendar_sync.models import CalendarConnection
from apps.calendar_sync.services import sync_connection
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun


class Command(CronHostCommand):
    help = "Pull external calendar events for every connected staff calendar."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--connection-id', type=int, default=None,
            help="Sync a single connection id (debugging); default: every CONNECTED connection.",
        )

    def handle(self, *args, **options):
        lock_name = 'sync_calendars'
        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name=lock_name)
            try:
                if options['connection_id']:
                    queryset = CalendarConnection.all_tenants.filter(
                        pk=options['connection_id'], deleted_at__isnull=True)
                else:
                    queryset = CalendarConnection.all_tenants.filter(
                        status=CalendarConnection.STATUS_CONNECTED, deleted_at__isnull=True)

                totals = {'ok': 0, 'failed': 0, 'created': 0, 'updated': 0, 'skipped': 0}
                for connection in queryset:
                    result = sync_connection(connection)
                    bucket = 'ok' if result['ok'] else 'failed'
                    totals[bucket] += 1
                    for key in ('created', 'updated', 'skipped'):
                        totals[key] += result.get(key, 0)
                    if not result['ok']:
                        self.stdout.write(self.style.WARNING(
                            f"connection {connection.pk} ({connection.provider}): {result['error']}"))

                job_run.items_processed = totals['ok'] + totals['failed']
                job_run.status = 'SUCCESS'
                job_run.save(update_fields=['items_processed', 'status'])
                self.stdout.write(self.style.SUCCESS(
                    f"sync_calendars: {totals['ok']} synced, {totals['failed']} failed, "
                    f"+{totals['created']} new / ~{totals['updated']} updated events"))
            except Exception as exc:
                job_run.status = 'FAILED'
                job_run.error_text = str(exc)[:2000]
                job_run.save(update_fields=['status', 'error_text'])
                raise

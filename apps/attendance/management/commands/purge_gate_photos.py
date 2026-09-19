"""Gate-photo retention sweeper (spec/14 §3/§7, CMP-013).

Purges GateEvent.photo_key for events older than the retention window
(default 90 days, spec/14's CMP-012 "gate photos retained 90 days by
default"). Biometric-template purge is a separate command,
`purge_biometric_templates` (apps.hardware), since it has its own trigger
conditions (consent withdrawal, subject exit) rather than a fixed age
window — this command only touches GateEvent.photo_key.
"""
from datetime import timedelta

from django.utils import timezone

from apps.attendance.models import GateEvent, GateEventArchive
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from educore.middleware.tenancy import tenant_context

GATE_PHOTO_RETENTION_DAYS = 90  # spec/14 §7 criterion 4


class Command(CronHostCommand):
    help = "Purge GateEvent.photo_key older than the retention window (default 90 days, CMP-013)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--retention-days', type=int, default=GATE_PHOTO_RETENTION_DAYS,
            help=f"Days to retain gate photos (default {GATE_PHOTO_RETENTION_DAYS}).",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help="List which rows would be purged without deleting anything.",
        )

    def handle(self, *args, **options):
        retention_days = options['retention_days']
        dry_run = options['dry_run']
        lock_name = 'educore:purge_gate_photos'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name='purge_gate_photos')
            try:
                cutoff = timezone.now() - timedelta(days=retention_days)

                # GateEvent is a TenantModel: GateEvent.objects (TenantManager)
                # fails closed to .none() when no thread-local foundation is
                # set. Cron invokes this command bare, so the sweep MUST enter
                # an explicit tenant_context per foundation (same pattern as
                # mark_absent_students) or it silently purges nothing forever.
                foundation_ids = list(
                    GateEvent.all_tenants
                    .exclude(photo_key='')
                    .values_list('foundation_id', flat=True)
                    .distinct()
                    .order_by('foundation_id')
                )

                total = 0
                for foundation_id in foundation_ids:
                    with tenant_context(foundation_id):
                        candidates = GateEvent.objects.exclude(photo_key='').filter(occurred_at__lt=cutoff)

                        if dry_run:
                            count = candidates.count()
                            for event in candidates[:50]:
                                self.stdout.write(
                                    f"Would purge photo_key for GateEvent#{event.id} ({event.occurred_at}) "
                                    f"[foundation {foundation_id}]"
                                )
                            total += count
                        else:
                            total += candidates.update(photo_key='')

                # Events already moved to gate_events_archive (NFR-009) keep their photo_key
                # until blanked here. The archive table is not tenant-scoped and this is an
                # age-only sweep, so no per-foundation context is needed.
                archived = GateEventArchive.objects.exclude(photo_key='').filter(occurred_at__lt=cutoff)
                total += archived.count() if dry_run else archived.update(photo_key='')

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f"purge_gate_photos --dry-run: {total} gate photo(s) older than {retention_days}d "
                        f"would be purged across {len(foundation_ids)} foundation(s)."))
                    job_run.status = JobRun.STATUS_SUCCESS
                    job_run.items_processed = 0
                    job_run.finished_at = timezone.now()
                    job_run.save()
                    return

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = total
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"purge_gate_photos: purged {total} gate photo(s) older than {retention_days}d "
                    f"across {len(foundation_ids)} foundation(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

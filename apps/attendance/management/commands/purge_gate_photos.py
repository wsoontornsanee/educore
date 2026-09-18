"""Gate-photo retention sweeper (spec/14 §3/§7, CMP-013).

Purges GateEvent.photo_key for events older than the retention window
(default 90 days, spec/14's CMP-012 "gate photos retained 90 days by
default"). Biometric-template purge is deliberately NOT included: no
face-template model exists anywhere in this repo yet (tracked as its own
Notion Open Item) — this command only touches the one real target,
GateEvent.photo_key.
"""
from datetime import timedelta

from django.utils import timezone

from apps.attendance.models import GateEvent
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun

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
                candidates = GateEvent.objects.exclude(photo_key='').filter(occurred_at__lt=cutoff)
                count = candidates.count()

                if dry_run:
                    for event in candidates[:50]:
                        self.stdout.write(f"Would purge photo_key for GateEvent#{event.id} ({event.occurred_at})")
                    self.stdout.write(self.style.SUCCESS(
                        f"purge_gate_photos --dry-run: {count} gate photo(s) older than {retention_days}d would be purged."))
                    job_run.status = JobRun.STATUS_SUCCESS
                    job_run.items_processed = 0
                    job_run.finished_at = timezone.now()
                    job_run.save()
                    return

                updated = candidates.update(photo_key='')

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = updated
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"purge_gate_photos: purged {updated} gate photo(s) older than {retention_days}d."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

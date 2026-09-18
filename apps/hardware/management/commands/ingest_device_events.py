"""Management command: apply pending staged device events (spec/01 §5.1, spec/12 §3).

Runs every minute (deploy/crontab):
    * * * * *      educore ingest_device_events

Edge agents upload raw event batches to POST /device/events, which stages
them in DeviceEventStaging without applying anything inline (HW-005) - this
command applies pending rows to domain tables (attendance.GateEvent and
downstream attendance derivation) via apps.hardware.services.ingest_device_events.
"""
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.hardware.services import ingest_device_events


class Command(CronHostCommand):
    help = "Apply pending staged device events to domain tables (HW-005, ARC-006)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--school-id',
            type=int,
            default=None,
            help="Only process staged events for this school (default: all schools).",
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=200,
            help="Maximum number of staged rows to apply this run (ARC-009, default: 200).",
        )

    def handle(self, *args, **options):
        with advisory_lock('ingest_device_events', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:ingest_device_events' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='ingest_device_events', status=JobRun.STATUS_RUNNING)

            try:
                result = ingest_device_events(
                    school_id=options.get('school_id'),
                    limit=options['batch_size'],
                )
                job_run.finished_at = timezone.now()
                job_run.items_processed = result['checked']
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"Checked {result['checked']} staged event(s): "
                    f"applied={result['applied']} failed={result['failed']}"
                ))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

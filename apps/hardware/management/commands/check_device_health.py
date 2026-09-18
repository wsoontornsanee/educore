"""Management command: mark stalled devices offline and alert (spec/01 §5.1, spec/12 §3, §4).

Runs every 10 minutes (deploy/crontab):
    */10 * * * *   educore check_device_health

Inspects Device.last_heartbeat_at (HW-007); a device exceeding
--timeout-minutes without a heartbeat is marked OFFLINE (HW-013) and, if it's
a gate/face terminal going offline during operational hours, the school admin
is alerted. Recovery to ONLINE happens automatically on the next heartbeat
via DeviceViewSet.heartbeat - not this command's concern.
"""
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.hardware.services import check_device_health


class Command(CronHostCommand):
    help = "Mark devices with a stale heartbeat OFFLINE and alert on critical gates (HW-007/HW-013, ARC-006)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--timeout-minutes',
            type=int,
            default=15,
            help="Minutes without a heartbeat before a device is marked OFFLINE (default: 15, HW-013).",
        )

    def handle(self, *args, **options):
        with advisory_lock('check_device_health', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:check_device_health' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='check_device_health', status=JobRun.STATUS_RUNNING)

            try:
                result = check_device_health(timeout_minutes=options['timeout_minutes'])
                job_run.finished_at = timezone.now()
                job_run.items_processed = result['checked']
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"Checked {result['checked']} stale device(s): "
                    f"marked_offline={result['marked_offline']} alerts_dispatched={result['alerts_dispatched']}"
                ))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

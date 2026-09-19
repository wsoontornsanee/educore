"""Management command: the school-bus unaccounted-student check (spec/05 §6, ATT-021).

Runs every minute (deploy/crontab). It closes runs the driver forgot to end, then checks every ended run that has
not been checked yet: a student whose last event on the run is BOARD raises an alert to the school's admins and the
student's guardians. Ending a run already checks it inline, so this is the safety net for forgotten runs and for
alerts that could not be queued the first time; the check is idempotent, so a run is never alerted twice.
"""
import logging

from django.utils import timezone

from apps.attendance.transport import check_unaccounted, close_stale_runs, unchecked_completed_runs
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.identity.models import Foundation
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Close forgotten bus runs and alert for students boarded but not alighted (ATT-021)."

    def handle(self, *args, **options):
        lock_name = 'check_unaccounted_students'
        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(f"Advisory lock 'educore:{lock_name}' already held. Exiting."))
                return

            job_run = JobRun.objects.create(job_name=lock_name, status=JobRun.STATUS_RUNNING)
            closed = checked = alerted = 0
            errors = []
            now = timezone.now()
            for foundation in Foundation.objects.filter(status=Foundation.STATUS_ACTIVE):
                try:
                    with tenant_context(foundation.id):
                        closed += close_stale_runs(foundation.id, now=now)
                        for run in unchecked_completed_runs(foundation.id, now=now):
                            student_ids = check_unaccounted(run, now=now)
                            checked += 1
                            alerted += len(student_ids or [])
                except Exception as exc:  # noqa: BLE001 - one foundation must not stop the others
                    logger.exception("check_unaccounted_students failed for foundation %s", foundation.id)
                    errors.append(f"foundation {foundation.id}: {type(exc).__name__}")

            job_run.finished_at = timezone.now()
            job_run.items_processed = checked
            job_run.status = JobRun.STATUS_FAILED if errors else JobRun.STATUS_SUCCESS
            job_run.error_text = "\n".join(errors)
            job_run.save(update_fields=['finished_at', 'items_processed', 'status', 'error_text'])
            self.stdout.write(self.style.SUCCESS(
                f"Closed {closed} stale run(s); checked {checked} run(s), {alerted} unaccounted student(s)."
            ))

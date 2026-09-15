"""Batch auto-submit for expired exam attempts (spec/04 §6, §9, ACD-025, ACD-026).

Runs every minute (deploy/crontab) so no IN_PROGRESS attempt sits unscored
past its exam window — the mechanism that actually makes the acceptance
criterion ("a 40-question MCQ exam for 300 students auto-scores within 60
seconds of window close") achievable, regardless of whether any student
ever revisits the exam URL after time runs out.
"""
import logging
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.academic.services import auto_submit_expired_attempts

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Force-submit every IN_PROGRESS exam attempt whose window has closed (ACD-025, ACD-026)."

    def handle(self, *args, **options):
        with advisory_lock('auto_submit_expired_exam_attempts', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:auto_submit_expired_exam_attempts' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='auto_submit_expired_exam_attempts', status=JobRun.STATUS_RUNNING)

            try:
                result = auto_submit_expired_attempts()
                job_run.finished_at = timezone.now()
                job_run.items_processed = result['submitted']
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.save()
                self.stdout.write(self.style.SUCCESS(f"Auto-submitted {result['submitted']} expired attempt(s)."))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.finished_at = timezone.now()
                job_run.error_text = str(e)
                job_run.save()
                raise

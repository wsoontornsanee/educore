"""Counsellor follow-up task reminders (spec/10 §5, LIF-018, deploy/crontab).

Sweeps `CounsellingSession` rows whose `follow_up_at` has arrived and whose
reminder has not yet been sent, and dispatches exactly one reminder per
session to its authoring counsellor.
"""
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.campus.services import get_counsellor_followups_due, queue_counsellor_followup_reminder


class Command(CronHostCommand):
    help = "Dispatches counsellor task reminders for counselling sessions whose follow-up date has arrived (LIF-018)."

    def handle(self, *args, **options):
        lock_name = 'educore:send_counsellor_followup_reminders'
        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name='send_counsellor_followup_reminders', status=JobRun.STATUS_RUNNING)
            try:
                sessions = list(get_counsellor_followups_due())
                reminded = 0
                for session in sessions:
                    if queue_counsellor_followup_reminder(session):
                        reminded += 1

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = reminded
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"send_counsellor_followup_reminders: sent {reminded} reminder(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

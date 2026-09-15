import traceback
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.notifications.models import IntentStatus, NotificationIntent
from apps.notifications.services import process_intent


class Command(BaseCommand):
    help = "Dispatches due and scheduled notifications released from quiet hours (spec/13 §2, ARC-007, ARC-008)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=200,
            help="Maximum number of due notification intents to process in one tick (default: 200)."
        )

    def handle(self, *args, **options):
        limit = options['limit']
        lock_name = 'educore:send_due_notifications'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(f"Advisory lock '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(
                job_name='send_due_notifications',
                started_at=timezone.now(),
                status=JobRun.STATUS_RUNNING,
            )

            processed_count = 0
            has_error = False
            last_error_text = ''

            try:
                now = timezone.now()
                due_intents = list(
                    NotificationIntent.all_tenants.filter(
                        status=IntentStatus.PENDING,
                        scheduled_for__lte=now,
                        deleted_at__isnull=True,
                    ).order_by('scheduled_for')[:limit]
                )

                for intent in due_intents:
                    success = process_intent(intent.id)
                    if success:
                        processed_count += 1

                job_run.status = JobRun.STATUS_SUCCESS
                self.stdout.write(self.style.SUCCESS(f"Successfully processed {processed_count} due notification intents."))

            except Exception as exc:
                has_error = True
                last_error_text = traceback.format_exc()
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = last_error_text
                self.stderr.write(self.style.ERROR(f"Error processing notifications: {exc}"))

            finally:
                job_run.finished_at = timezone.now()
                job_run.items_processed = processed_count
                if has_error and not job_run.error_text:
                    job_run.error_text = last_error_text
                job_run.save()

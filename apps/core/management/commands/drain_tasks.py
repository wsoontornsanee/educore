"""Drain TaskQueue command.

Implements:
- ARC-007: MySQL named advisory lock (educore:drain_tasks)
- ARC-008: core.JobRun logging
- ARC-009: Bounded batches via --limit
- ARC-010, ARC-011: SELECT ... FOR UPDATE SKIP LOCKED
- ARC-012: Exponential backoff (1m, 5m, 15m, 60m) and DEAD_LETTER state
"""
import socket
import traceback
from datetime import timedelta
from apps.core.management.base import CronHostCommand
from django.db import connection, transaction
from django.utils import timezone
from apps.core.locks import advisory_lock
from apps.core.models import JobRun, TaskQueue
from apps.core.services import get_task_handler
from educore.middleware.tenancy import tenant_context

BACKOFF_INTERVALS = {
    1: timedelta(minutes=1),
    2: timedelta(minutes=5),
    3: timedelta(minutes=15),
    4: timedelta(minutes=60),
}

class Command(CronHostCommand):
    help = "Drains pending asynchronous tasks from TaskQueue (ARC-010, ARC-011, ARC-012)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--limit',
            type=int,
            default=200,
            help="Maximum number of tasks to process in a single batch (default: 200)."
        )

    def handle(self, *args, **options):
        limit = options['limit']
        lock_name = 'drain_tasks'
        worker_id = f"{socket.gethostname()}_{timezone.now().timestamp()}"

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(
                job_name='drain_tasks',
                started_at=timezone.now(),
                status=JobRun.STATUS_RUNNING,
            )

            processed_count = 0
            has_error = False
            last_error_text = ''

            try:
                now = timezone.now()
                # 1. Claim batch of tasks with SELECT ... FOR UPDATE SKIP LOCKED
                with transaction.atomic():
                    qs = TaskQueue.objects.filter(
                        status=TaskQueue.STATUS_PENDING,
                        run_after__lte=now
                    ).order_by('run_after')

                    if connection.features.has_select_for_update_skip_locked:
                        tasks = list(qs.select_for_update(skip_locked=True)[:limit])
                    else:
                        tasks = list(qs.select_for_update()[:limit])

                    # Mark claimed tasks as RUNNING
                    for task in tasks:
                        task.status = TaskQueue.STATUS_RUNNING
                        task.locked_at = now
                        task.locked_by = worker_id
                        task.save(update_fields=['status', 'locked_at', 'locked_by', 'updated_at'])

                # 2. Process tasks individually
                for task in tasks:
                    self._process_single_task(task)
                    processed_count += 1

                job_run.status = JobRun.STATUS_SUCCESS
                self.stdout.write(self.style.SUCCESS(f"Successfully processed {processed_count} tasks."))

            except Exception as exc:
                has_error = True
                last_error_text = traceback.format_exc()
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = last_error_text
                self.stderr.write(self.style.ERROR(f"Error draining tasks: {exc}"))

            finally:
                job_run.finished_at = timezone.now()
                job_run.items_processed = processed_count
                if has_error and not job_run.error_text:
                    job_run.error_text = last_error_text
                job_run.save()

    def _process_single_task(self, task: TaskQueue):
        handler = get_task_handler(task.task_type)
        now = timezone.now()

        # Run task inside its foundation context if foundation_id is set
        context_manager = tenant_context(task.foundation_id) if task.foundation_id else transaction.atomic()

        try:
            with context_manager:
                if not handler:
                    raise ValueError(f"No handler registered for task type: '{task.task_type}'")
                handler(task.payload)

            task.status = TaskQueue.STATUS_COMPLETED
            task.locked_at = None
            task.locked_by = None
            task.error_text = ''
            task.save(update_fields=['status', 'locked_at', 'locked_by', 'error_text', 'updated_at'])

        except Exception as exc:
            task.attempts += 1
            error_details = traceback.format_exc()
            task.error_text = error_details

            if task.attempts >= task.max_attempts:
                task.status = TaskQueue.STATUS_DEAD_LETTER
                task.locked_at = None
                task.locked_by = None
            else:
                task.status = TaskQueue.STATUS_PENDING
                backoff = BACKOFF_INTERVALS.get(task.attempts, timedelta(minutes=60))
                task.run_after = now + backoff
                task.locked_at = None
                task.locked_by = None

            task.save(update_fields=['attempts', 'status', 'run_after', 'locked_at', 'locked_by', 'error_text', 'updated_at'])
            self.stderr.write(self.style.WARNING(f"Task #{task.id} failed (attempt {task.attempts}/{task.max_attempts}): {exc}"))

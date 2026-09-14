"""Tests for TaskQueue and drain_tasks management command (ARC-010, ARC-011, ARC-012)."""
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from apps.core.models import JobRun, TaskQueue
from apps.core.services import enqueue_task, register_task_handler

class TaskQueueTests(TestCase):
    def setUp(self):
        TaskQueue.objects.all().delete()
        JobRun.objects.all().delete()

    def test_enqueue_task_helper(self):
        """Verify enqueue_task creates a TaskQueue row with PENDING status."""
        task = enqueue_task(
            task_type="test.sample.action",
            payload={"student_id": 42},
            foundation_id=101,
        )
        self.assertEqual(task.status, TaskQueue.STATUS_PENDING)
        self.assertEqual(task.foundation_id, 101)
        self.assertEqual(task.payload, {"student_id": 42})
        self.assertEqual(task.attempts, 0)
        self.assertEqual(task.max_attempts, 4)

    def test_drain_tasks_success(self):
        """Verify drain_tasks executes registered handler and marks task completed."""
        executed = []

        @register_task_handler("test.sample.success")
        def sample_handler(payload):
            executed.append(payload["msg"])

        task = enqueue_task(
            task_type="test.sample.success",
            payload={"msg": "halo dunia"},
            foundation_id=101,
        )

        call_command('drain_tasks', limit=10)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskQueue.STATUS_COMPLETED)
        self.assertIn("halo dunia", executed)

        # Check JobRun recorded
        job_run = JobRun.objects.filter(job_name='drain_tasks').last()
        self.assertIsNotNone(job_run)
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_drain_tasks_retry_and_dead_letter(self):
        """Verify drain_tasks handles failures with retries and dead-letter state (ARC-012)."""
        @register_task_handler("test.sample.failing")
        def failing_handler(payload):
            raise RuntimeError("Temporary simulated failure")

        task = enqueue_task(
            task_type="test.sample.failing",
            payload={"foo": "bar"},
            foundation_id=101,
            max_attempts=2,
        )

        # 1st attempt
        call_command('drain_tasks', limit=10)
        task.refresh_from_db()
        self.assertEqual(task.attempts, 1)
        self.assertEqual(task.status, TaskQueue.STATUS_PENDING)
        self.assertGreater(task.run_after, timezone.now())
        self.assertIn("Temporary simulated failure", task.error_text)

        # Force run_after to past to simulate time passing
        task.run_after = timezone.now() - timezone.timedelta(seconds=10)
        task.save()

        # 2nd attempt -> should reach max_attempts (2) and become DEAD_LETTER
        call_command('drain_tasks', limit=10)
        task.refresh_from_db()
        self.assertEqual(task.attempts, 2)
        self.assertEqual(task.status, TaskQueue.STATUS_DEAD_LETTER)

"""One ``core.JobRun`` row per scheduled run (spec/01 ARC-008).

    with track_job_run('generate_invoices', record=not dry_run) as run:
        ...
        run.items_processed += 1        # what the job did
        run.add_error("school 3: ...")  # a failure the job survived; the run ends FAILED

An exception escaping the block also ends the run FAILED (with the exception as ``error_text``)
and is re-raised, so cron's own failure signal is unchanged. Job names are the bare command
name, matching the existing hand-rolled writers.
"""
from contextlib import contextmanager
from typing import Iterator, Optional

from django.utils import timezone

from apps.core.models import JobRun

# error_text is a TEXT column (64 KB on MySQL); a per-school error list can grow without bound.
MAX_ERROR_TEXT_CHARS = 10_000


class JobRunTracker:
    """What the running job reports back; persisted when the ``track_job_run`` block ends."""

    def __init__(self) -> None:
        self.items_processed = 0
        self.errors: list = []

    def add_error(self, message) -> None:
        self.errors.append(str(message))


def _finish(job_run: Optional[JobRun], tracker: JobRunTracker) -> None:
    if job_run is None:
        return
    job_run.finished_at = timezone.now()
    job_run.items_processed = tracker.items_processed
    job_run.status = JobRun.STATUS_FAILED if tracker.errors else JobRun.STATUS_SUCCESS
    job_run.error_text = "\n".join(tracker.errors)[:MAX_ERROR_TEXT_CHARS]
    job_run.save(update_fields=['finished_at', 'items_processed', 'status', 'error_text'])


@contextmanager
def track_job_run(job_name: str, *, record: bool = True) -> Iterator[JobRunTracker]:
    """Record a JobRun around the block. ``record=False`` (dry runs) yields a tracker and writes nothing.

    Open the block only after arguments are validated: a usage error is not a failed scheduled run.
    """
    tracker = JobRunTracker()
    job_run = (
        JobRun.objects.create(job_name=job_name, status=JobRun.STATUS_RUNNING, started_at=timezone.now())
        if record else None
    )
    try:
        yield tracker
    except Exception as exc:
        tracker.add_error(f"{type(exc).__name__}: {exc}")
        _finish(job_run, tracker)
        raise
    _finish(job_run, tracker)

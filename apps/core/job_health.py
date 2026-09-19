"""Is each scheduled job still succeeding? (spec/01 ARC-008, second sentence)

    "A job that has not succeeded within its expected window MUST raise an alert on the ops dashboard."

`evaluate_job_health()` reads `core.JobRun` and answers per job, at call time. It deliberately does not
depend on a cron run of its own: the ops page evaluates it on every render, so it still tells the truth
when the cron host is the thing that died. `check_job_freshness` (cron) uses it to send the alert; that
half cannot fire if cron is entirely down, which only an external monitor can cover.

Windows are expressed once, per cadence tier, and every scheduled job must appear in exactly one tier
(`test_job_health` checks that against `deploy/crontab`), so a new job cannot be scheduled without saying
how stale is too stale. `max_age` is the interval plus generous slack: it separates "missed a run or two"
from "stopped", it is not an SLA.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from django.utils import timezone

from apps.core.models import JobRun

# Tier name -> (max age of the last SUCCESS, the job_names on that cadence). Job names are what each
# command writes to JobRun.job_name: the command name, except refresh_reporting which suffixes its scope.
CADENCE_TIERS = {
    'every minute': (timedelta(minutes=15), [
        'drain_tasks', 'ingest_device_events', 'auto_submit_expired_exam_attempts', 'check_unaccounted_students',
    ]),
    'every 5 minutes': (timedelta(minutes=30), [
        'refresh_reporting_dashboard', 'sync_payment_status', 'check_service_health',
    ]),
    'every 10 minutes': (timedelta(minutes=45), [
        'check_device_health',
    ]),
    'every 15 minutes': (timedelta(hours=1), [
        'send_due_notifications', 'sync_calendars', 'check_job_freshness',
    ]),
    'every 30 minutes': (timedelta(hours=2), [
        'send_wallet_recon_reminders', 'send_counsellor_followup_reminders', 'process_wallet_auto_topups',
    ]),
    'hourly': (timedelta(hours=3), [
        'expire_sessions', 'sync_rbac_sheet',
    ]),
    'daily': (timedelta(hours=26), [
        'pull_bank_statements', 'mark_absent_students', 'remind_library_loans', 'send_digests',
        'reconcile_payments', 'reconcile_wallet_balances', 'refresh_reporting_full', 'run_arrears_ladder',
        'enforce_retention', 'purge_gate_photos', 'purge_biometric_templates', 'backup_database',
        'invoice_stale_reconciliations', 'archive_high_write_tables',
    ]),
    'weekly': (timedelta(days=8), [
        'settle_merchants',
    ]),
    'monthly': (timedelta(days=33), [
        'generate_invoices', 'close_fiscal_periods',
    ]),
}

JOB_MAX_AGE = {job: max_age for max_age, jobs in CADENCE_TIERS.values() for job in jobs}
JOB_CADENCE = {job: tier for tier, (_max_age, jobs) in CADENCE_TIERS.items() for job in jobs}

STATE_OK = 'OK'
STATE_STALE = 'STALE'          # no SUCCESS inside the window
STATE_STUCK = 'STUCK'          # a run started before the window opened and never finished (killed mid-run)
STATE_NEVER_RUN = 'NEVER_RUN'  # no JobRun rows at all: a fresh deploy, or a job not run in this environment

ALERT_STATES = (STATE_STALE, STATE_STUCK)


@dataclass(frozen=True)
class JobHealth:
    job_name: str
    cadence: str
    max_age: timedelta
    state: str
    last_run_at: Optional[datetime]
    last_status: str
    last_success_at: Optional[datetime]
    last_error: str

    @property
    def needs_alert(self) -> bool:
        return self.state in ALERT_STATES


def _evaluate(job_name: str, now: datetime) -> JobHealth:
    max_age = JOB_MAX_AGE[job_name]
    runs = JobRun.objects.filter(job_name=job_name)
    last_run = runs.order_by('-started_at', '-id').first()
    last_success = runs.filter(status=JobRun.STATUS_SUCCESS).order_by('-started_at', '-id').first()
    last_success_at = (last_success.finished_at or last_success.started_at) if last_success else None
    last_failure = runs.filter(status=JobRun.STATUS_FAILED).order_by('-started_at', '-id').first()

    if last_run is None:
        state = STATE_NEVER_RUN
    elif last_run.status == JobRun.STATUS_RUNNING and last_run.started_at < now - max_age:
        state = STATE_STUCK
    elif last_success_at is not None and last_success_at >= now - max_age:
        state = STATE_OK
    elif last_run.status == JobRun.STATUS_RUNNING:
        state = STATE_OK  # young run in flight: about to be fresh, not stale
    else:
        state = STATE_STALE

    return JobHealth(
        job_name=job_name, cadence=JOB_CADENCE[job_name], max_age=max_age, state=state,
        last_run_at=last_run.started_at if last_run else None,
        last_status=last_run.status if last_run else '',
        last_success_at=last_success_at,
        last_error=(last_failure.error_text if last_failure else '')[:300],
    )


def evaluate_job_health(now: Optional[datetime] = None) -> list:
    """One JobHealth per known scheduled job, unhealthy first, then by name."""
    now = now or timezone.now()
    rows = [_evaluate(job_name, now) for job_name in JOB_MAX_AGE]
    order = {STATE_STUCK: 0, STATE_STALE: 1, STATE_NEVER_RUN: 2, STATE_OK: 3}
    return sorted(rows, key=lambda r: (order[r.state], r.job_name))

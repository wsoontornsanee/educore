"""Cron: alert platform operators when a scheduled job has stopped succeeding (*/15, deploy/crontab; ARC-008)."""
from apps.core.job_health import evaluate_job_health
from apps.core.job_runs import track_job_run
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.status.services import dispatch_job_alerts


class Command(CronHostCommand):
    help = "Email platform operators about scheduled jobs with no SUCCESS inside their expected window (ARC-008)."

    def handle(self, *args, **options):
        with advisory_lock('check_job_freshness', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:check_job_freshness' already held. Exiting."
                ))
                return

            with track_job_run('check_job_freshness') as run:
                rows = evaluate_job_health()
                unhealthy = [row for row in rows if row.needs_alert]
                alerted = dispatch_job_alerts(rows)
                run.items_processed = len(unhealthy)

            for row in unhealthy:
                self.stdout.write(self.style.WARNING(f"  {row.job_name}: {row.state}"))
            self.stdout.write(self.style.SUCCESS(
                f"Checked {len(rows)} scheduled job(s): {len(unhealthy)} need attention, {alerted} newly alerted."
            ))

"""Cron: push the live RBAC visibility matrix to the RBAC management Google Sheet (hourly, deploy/crontab)."""
from django.conf import settings
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.identity.rbac_matrix import build_matrix
from apps.identity.rbac_sheet import SheetNotConfigured, build_service, sync_matrix


class Command(CronHostCommand):
    help = "Sync roles/menus/surfaces access matrix to the RBAC Google Sheet (no-op when unchanged or unconfigured)."

    def handle(self, *args, **options):
        with advisory_lock('sync_rbac_sheet', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING("Advisory lock 'sync_rbac_sheet' already held. Exiting."))
                return
            try:
                service = build_service()
            except SheetNotConfigured as exc:
                self.stdout.write(f"sync_rbac_sheet: not configured ({exc}), skipping")
                return
            # ARC-008. Created only once configured: an unconfigured dev/CI no-op is not a run.
            job_run = JobRun.objects.create(job_name='sync_rbac_sheet', status=JobRun.STATUS_RUNNING)
            try:
                changed = sync_matrix(service, settings.RBAC_SHEET_ID, build_matrix(), timezone.now().isoformat())
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save(update_fields=['status', 'error_text', 'finished_at'])
                raise
            job_run.status = JobRun.STATUS_SUCCESS
            job_run.items_processed = 1 if changed else 0  # 1 = sheet rewritten, 0 = unchanged
            job_run.finished_at = timezone.now()
            job_run.save(update_fields=['status', 'items_processed', 'finished_at'])
            self.stdout.write(self.style.SUCCESS(
                "sync_rbac_sheet: sheet rewritten" if changed else "sync_rbac_sheet: unchanged, no write"
            ))

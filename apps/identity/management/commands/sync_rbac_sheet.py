"""Cron: push the live RBAC visibility matrix to the RBAC management Google Sheet (hourly, deploy/crontab)."""
from django.conf import settings
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
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
            except SheetNotConfigured:
                self.stdout.write("sync_rbac_sheet: not configured (RBAC_SHEET_ID / RBAC_SHEET_SERVICE_ACCOUNT_JSON), skipping")
                return
            changed = sync_matrix(service, settings.RBAC_SHEET_ID, build_matrix(), timezone.now().isoformat())
            self.stdout.write(self.style.SUCCESS(
                "sync_rbac_sheet: sheet rewritten" if changed else "sync_rbac_sheet: unchanged, no write"
            ))

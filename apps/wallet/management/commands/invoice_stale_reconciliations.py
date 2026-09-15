"""7-day handover of unresolved wallet reconciliation cases to billing (spec/17 §8, REC-013,
deploy/crontab 0 1 * * *).

Still OPEN after 7 days: the shortfall is posted to the student's billing as a
PENYESUAIAN SALDO KANTIN line, the case marked INVOICED, and the normal arrears
ladder takes over from there.
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.wallet.services import get_reconciliations_due_for_invoice_handover, invoice_reconciliation_case

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Hands unresolved wallet reconciliation cases over to billing after 7 days (REC-013)."

    def handle(self, *args, **options):
        with advisory_lock('invoice_stale_reconciliations', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'educore:invoice_stale_reconciliations' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='invoice_stale_reconciliations', status=JobRun.STATUS_RUNNING)

            try:
                cases = list(get_reconciliations_due_for_invoice_handover())
                invoiced = 0
                for case in cases:
                    invoice_reconciliation_case(case)
                    invoiced += 1

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = invoiced
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(f"Invoiced {invoiced} stale reconciliation case(s)."))
            except Exception as e:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(e)
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

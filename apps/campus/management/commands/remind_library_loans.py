"""Library loan overdue-reminder sweeper (spec/10 §2/§6, spec/13 §3, deploy/crontab).

Sweeps ACTIVE loans past due_at per foundation, flips them OVERDUE, and
dispatches a LIBRARY_LOAN_DUE notification intent per borrower — digest_only,
so it is folded into that recipient's evening digest by send_digests without
any change needed there (same closed gap as PR #130's Open Item).
"""
from django.utils import timezone

from apps.campus.models import Loan, LoanStatus
from apps.campus.services import remind_overdue_loans
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from educore.middleware.tenancy import tenant_context


class Command(CronHostCommand):
    help = "Mark overdue library loans and dispatch LIBRARY_LOAN_DUE reminders (spec/10 §6)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--dry-run', action='store_true',
            help="List which loans would be marked overdue/reminded without mutating anything.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        lock_name = 'educore:remind_library_loans'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name='remind_library_loans')
            try:
                # Loan is a TenantModel: .objects fails closed to .none() without a
                # thread-local foundation, so loop foundations via tenant_context
                # (same pattern as purge_gate_photos/purge_biometric_templates).
                foundation_ids = list(
                    Loan.all_tenants
                    .filter(status=LoanStatus.ACTIVE, due_at__lt=timezone.now())
                    .values_list('foundation_id', flat=True)
                    .distinct()
                    .order_by('foundation_id')
                )

                total = 0
                for foundation_id in foundation_ids:
                    with tenant_context(foundation_id):
                        if dry_run:
                            overdue_count = Loan.objects.filter(
                                status=LoanStatus.ACTIVE, due_at__lt=timezone.now(),
                            ).count()
                            self.stdout.write(
                                f"Would remind {overdue_count} overdue loan(s) [foundation {foundation_id}]"
                            )
                            total += overdue_count
                        else:
                            result = remind_overdue_loans(foundation_id)
                            total += result['count']

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f"remind_library_loans --dry-run: {total} loan(s) would be reminded "
                        f"across {len(foundation_ids)} foundation(s)."))
                    job_run.status = JobRun.STATUS_SUCCESS
                    job_run.items_processed = 0
                    job_run.finished_at = timezone.now()
                    job_run.save()
                    return

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = total
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"remind_library_loans: reminded {total} loan(s) across "
                    f"{len(foundation_ids)} foundation(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

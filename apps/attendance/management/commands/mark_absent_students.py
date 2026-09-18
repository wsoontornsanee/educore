"""Django management command to execute the automated daily absence sweep (spec/05 §3, deploy/crontab:19).

Enforces:
- Advisory locking with educore:mark_absent_students (ARC-007, spec/01 §5).
- Cron host gating via CronHostCommand (ARC-013, spec/01 §7).
- Scheduled execution tracking via JobRun (ARC-008).
- Multi-tenancy boundary via with tenant_context(foundation_id).
- Daily status derivation: no gate scan by absent_cutoff and no approved request -> ALPA (ATT-001).
- Approved absence requests override to SAKIT/IZIN (ATT-002).
- Preservation of manual staff overrides and existing records (ATT-003).
- Non-school days (weekends, holidays) never generate ALPA (ATT-005).
- Dispatches parent alert notifications with deduplication (spec/13).
"""
import datetime
import logging
from typing import Optional

from django.utils import timezone

from apps.attendance.services import mark_absent_students_for_school
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.identity.models import Foundation, School
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)


class Command(CronHostCommand):
    help = "Run the automated daily absence sweep and mark unrecorded students as ALPA (spec/05 §3, ATT-001..005)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--date',
            type=str,
            help="Target calendar date in YYYY-MM-DD format (defaults to current date in school timezone).",
        )
        parser.add_argument(
            '--school-id',
            type=str,
            help="Execute absence sweep only for a specific school ID.",
        )
        parser.add_argument(
            '--foundation-id',
            type=str,
            help="Execute absence sweep only for a specific foundation ID.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Preview eligible students and count without writing records or sending notifications.",
        )
        parser.add_argument(
            '--force-cutoff',
            action='store_true',
            help="Bypass cutoff time check even if current local time is before absent_cutoff_time.",
        )

    def handle(self, *args, **options):
        lock_name = 'mark_absent_students'

        # 1. Acquire named advisory lock (ARC-007)
        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock 'educore:{lock_name}' already held. Exiting immediately."
                ))
                return

            date_str = options.get('date')
            target_date: Optional[datetime.date] = None
            if date_str:
                try:
                    target_date = datetime.date.fromisoformat(date_str)
                except ValueError:
                    self.stdout.write(self.style.ERROR(
                        f"Invalid date format: {date_str}. Expected YYYY-MM-DD."
                    ))
                    return

            dry_run = options.get('dry_run', False)
            force_cutoff = options.get('force_cutoff', False)
            school_id = options.get('school_id')
            foundation_id = options.get('foundation_id')

            # 2. Initialize JobRun tracking (ARC-008)
            job_run = None
            if not dry_run:
                job_run = JobRun.objects.create(
                    job_name=lock_name,
                    status=JobRun.STATUS_RUNNING,
                    started_at=timezone.now(),
                )

            self.stdout.write(
                f"Starting automated absence sweep (date={target_date or 'today_local'}, dry_run={dry_run}, force_cutoff={force_cutoff})"
            )

            foundations_qs = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)
            if foundation_id:
                foundations_qs = foundations_qs.filter(id=foundation_id)

            total_evaluated = 0
            total_already_recorded = 0
            total_excused = 0
            total_holiday_exempt = 0
            total_marked_alpa = 0
            total_notifications = 0
            error_messages = []

            for foundation in foundations_qs:
                with tenant_context(foundation.id):
                    schools_qs = School.objects.filter(foundation_id=foundation.id, is_active=True)
                    if school_id:
                        schools_qs = schools_qs.filter(id=school_id)

                    for school in schools_qs:
                        try:
                            res = mark_absent_students_for_school(
                                school=school,
                                target_date=target_date,
                                dry_run=dry_run,
                                force_cutoff=force_cutoff,
                            )
                            status_desc = res.get('status', 'UNKNOWN')
                            if status_desc in ('COMPLETED', 'DRY_RUN'):
                                total_evaluated += res.get('total_students', 0)
                                total_already_recorded += res.get('already_recorded', 0)
                                total_excused += res.get('excused_from_requests', 0)
                                total_holiday_exempt += res.get('holiday_exempt', 0)
                                total_marked_alpa += res.get('marked_alpa', 0)
                                total_notifications += res.get('notifications_dispatched', 0)

                                self.stdout.write(
                                    f"  [{school.name}] Students: {res.get('total_students', 0)}, "
                                    f"Already recorded: {res.get('already_recorded', 0)}, "
                                    f"Excused: {res.get('excused_from_requests', 0)}, "
                                    f"ALPA marked: {res.get('marked_alpa', 0)}, "
                                    f"Notified: {res.get('notifications_dispatched', 0)}"
                                )
                            else:
                                reason = res.get('reason', '')
                                self.stdout.write(
                                    f"  [{school.name}] Skipped: {status_desc} ({reason})"
                                )
                        except Exception as exc:
                            err_msg = f"Error processing school #{school.id} ({school.name}): {exc}"
                            logger.exception(err_msg)
                            error_messages.append(err_msg)
                            self.stdout.write(self.style.ERROR(err_msg))

            # 3. Finalize JobRun record
            if job_run:
                job_run.finished_at = timezone.now()
                job_run.items_processed = total_marked_alpa
                if error_messages:
                    job_run.status = JobRun.STATUS_FAILED
                    job_run.error_text = "\n".join(error_messages)
                else:
                    job_run.status = JobRun.STATUS_SUCCESS
                job_run.save(update_fields=['finished_at', 'items_processed', 'status', 'error_text'])

            dry_run_suffix = " (DRY RUN - No changes written)" if dry_run else ""
            self.stdout.write(self.style.SUCCESS(
                f"Absence sweep completed{dry_run_suffix}. Evaluated: {total_evaluated}, "
                f"Already Recorded: {total_already_recorded}, Excused: {total_excused}, "
                f"ALPA: {total_marked_alpa}, Notifications Sent: {total_notifications}"
            ))

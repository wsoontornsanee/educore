"""Biometric-template retention sweeper (spec/14 §3/§7, CMP-012/013).

`withdraw_biometric_consent` (apps.compliance.services) already deletes a
template synchronously the moment consent is withdrawn — this command is the
CMP-013 "not by hope" safety net that catches anything that path missed, plus
the CMP-012 "biometrics deleted on exit" case, which has no synchronous hook
(unlike Student.transition_status's wallet freeze/refund calls, there is no
equivalent lifecycle hook on Staff, so a uniform daily sweep covers both
subject types instead of two divergent mechanisms). Purges an ACTIVE
BiometricTemplate when either:

  1. its linked ConsentRecord has been withdrawn (safety net), or
  2. its subject (Student/Staff) is now in a departed/terminal status
     (exit-triggered deletion).
"""
from django.utils import timezone

from apps.compliance.models import ConsentRecord
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.hardware.models import BiometricTemplate, BiometricTemplateStatus
from apps.hardware.services import delete_biometric_template
from apps.identity.models import Staff, Student
from educore.middleware.tenancy import tenant_context

_STUDENT_EXIT_STATUSES = {Student.STATUS_GRADUATED, Student.STATUS_TRANSFERRED_OUT}
_STAFF_EXIT_STATUSES = {Staff.STATUS_OFFBOARDED}


class Command(CronHostCommand):
    help = "Purge ACTIVE BiometricTemplate rows whose consent was withdrawn or whose subject has exited (CMP-012/013)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--dry-run', action='store_true',
            help="List which templates would be purged without deleting anything.",
        )

    def _candidates_to_purge(self, foundation_id):
        templates = list(
            BiometricTemplate.objects.filter(status=BiometricTemplateStatus.ACTIVE)
        )
        if not templates:
            return []

        withdrawn_consent_ids = set(
            ConsentRecord.objects.filter(
                foundation_id=foundation_id, withdrawn_at__isnull=False,
                id__in=[t.consent_id for t in templates],
            ).values_list('id', flat=True)
        )

        student_ids = {t.subject_id for t in templates if t.subject_type == 'STUDENT'}
        exited_student_ids = set(
            Student.objects.filter(id__in=student_ids, status__in=_STUDENT_EXIT_STATUSES)
            .values_list('id', flat=True)
        )
        staff_ids = {t.subject_id for t in templates if t.subject_type == 'STAFF'}
        exited_staff_ids = set(
            Staff.objects.filter(id__in=staff_ids, status__in=_STAFF_EXIT_STATUSES)
            .values_list('id', flat=True)
        )

        result = []
        for template in templates:
            if template.consent_id in withdrawn_consent_ids:
                result.append((template, BiometricTemplateStatus.WITHDRAWN))
            elif template.subject_type == 'STUDENT' and template.subject_id in exited_student_ids:
                result.append((template, BiometricTemplateStatus.PURGED))
            elif template.subject_type == 'STAFF' and template.subject_id in exited_staff_ids:
                result.append((template, BiometricTemplateStatus.PURGED))
        return result

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        lock_name = 'educore:purge_biometric_templates'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name='purge_biometric_templates')
            try:
                # BiometricTemplate is a TenantModel: .objects fails closed to
                # .none() without a thread-local foundation, same reason
                # purge_gate_photos loops per-foundation via tenant_context.
                foundation_ids = list(
                    BiometricTemplate.all_tenants
                    .filter(status=BiometricTemplateStatus.ACTIVE)
                    .values_list('foundation_id', flat=True)
                    .distinct()
                    .order_by('foundation_id')
                )

                total = 0
                for foundation_id in foundation_ids:
                    with tenant_context(foundation_id):
                        candidates = self._candidates_to_purge(foundation_id)

                        if dry_run:
                            for template, reason in candidates:
                                self.stdout.write(
                                    f"Would purge BiometricTemplate#{template.id} "
                                    f"({template.subject_type}#{template.subject_id}) as {reason} "
                                    f"[foundation {foundation_id}]"
                                )
                            total += len(candidates)
                        else:
                            for template, reason in candidates:
                                delete_biometric_template(template, reason)
                            total += len(candidates)

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f"purge_biometric_templates --dry-run: {total} template(s) would be purged "
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
                    f"purge_biometric_templates: purged {total} template(s) across "
                    f"{len(foundation_ids)} foundation(s)."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise

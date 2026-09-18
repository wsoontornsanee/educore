import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.core.services import audit, record_domain_event
from apps.identity.models import School, Staff, Student
from .models import ClinicOutcome, ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock

logger = logging.getLogger(__name__)


def get_or_create_clinic_policy(school: School) -> ClinicPolicy:
    """Gets or creates the ClinicPolicy for a given school (LIF-006)."""
    policy, _ = ClinicPolicy.objects.get_or_create(
        foundation_id=school.foundation_id,
        school=school,
        defaults={'teacher_sees_allergies': True},
    )
    return policy


def get_or_create_health_profile(student: Student) -> HealthProfile:
    """Gets or creates the HealthProfile for a given student (LIF-002)."""
    profile, _ = HealthProfile.objects.get_or_create(
        foundation_id=student.foundation_id,
        student=student,
    )
    return profile


def get_medication_stock_alerts(school: School):
    """LIF-005: stock rows below reorder_level or within 30 days of expiry."""
    today = timezone.now().date()
    horizon = today + timezone.timedelta(days=30)
    return MedicationStock.objects.filter(
        foundation_id=school.foundation_id,
        school=school,
        deleted_at__isnull=True,
    ).filter(
        Q(quantity__lt=F('reorder_level')) | Q(expiry_date__lte=horizon)
    ).order_by('expiry_date')


def record_clinic_visit(
    foundation_id: int,
    school: School,
    student: Student,
    handled_by: Staff,
    complaint: str,
    outcome: str,
    treatment: str = '',
    vitals: dict = None,
    medication: MedicationStock = None,
    medication_quantity: int = None,
    guardian_consent_confirmed: bool = False,
    guardian_consent_note: str = '',
    occurred_at=None,
) -> ClinicVisit:
    """Records a clinic visit end-to-end (LIF-001, LIF-003, LIF-004, LIF-007)."""
    from .crypto import encrypt_note

    if outcome not in ClinicOutcome.values:
        raise ValidationError(f"Outcome klinik tidak valid: {outcome}")
    if student.school_id != school.id:
        raise ValidationError("Siswa tidak terdaftar di sekolah yang bersangkutan.")
    if not complaint or not complaint.strip():
        raise ValidationError("Keluhan wajib diisi.")

    if occurred_at is None:
        occurred_at = timezone.now()
    vitals = vitals or {}

    with transaction.atomic():
        if medication is not None:
            if medication.school_id != school.id:
                raise ValidationError("Stok obat tidak sesuai dengan sekolah yang bersangkutan.")
            if not medication_quantity or medication_quantity <= 0:
                raise ValidationError("Jumlah obat yang diberikan wajib diisi.")

            from apps.compliance.models import DataSubjectRequestSubjectType
            from apps.compliance.services import has_active_health_consent

            has_standing_consent = has_active_health_consent(
                subject_type=DataSubjectRequestSubjectType.STUDENT,
                subject_id=student.id,
                foundation_id=foundation_id,
            )
            if not has_standing_consent and not guardian_consent_confirmed:
                raise ValidationError(
                    "Pemberian obat memerlukan persetujuan wali (consent standing atau konfirmasi per-insiden)."
                )

            stock = MedicationStock.all_tenants.select_for_update().get(
                pk=medication.pk, foundation_id=foundation_id
            )
            if stock.quantity - medication_quantity < 0:
                raise ValidationError(f"Stok obat '{stock.name}' tidak mencukupi.")
            stock.quantity = stock.quantity - medication_quantity
            stock.save(update_fields=['quantity', 'updated_at'])

        visit = ClinicVisit.objects.create(
            foundation_id=foundation_id,
            school=school,
            student=student,
            occurred_at=occurred_at,
            complaint_encrypted=encrypt_note(complaint.strip()),
            treatment_encrypted=encrypt_note(treatment.strip()) if treatment and treatment.strip() else '',
            vitals=vitals,
            medication_given=medication,
            medication_quantity_used=medication_quantity if medication is not None else None,
            outcome=outcome,
            handled_by=handled_by,
            guardian_consent_confirmed=guardian_consent_confirmed,
            guardian_consent_note=guardian_consent_note or '',
        )

        audit(
            action='campus.clinic_visit.recorded',
            entity_type='ClinicVisit',
            entity_id=str(visit.id),
            actor_id=str(handled_by.user_id) if handled_by else None,
            foundation_id=foundation_id,
            school_id=school.id,
            diff={
                'student_id': student.id,
                'outcome': outcome,
                'medication_id': medication.id if medication else None,
            },
        )
        record_domain_event(
            name='campus.clinic_visit.recorded',
            foundation_id=foundation_id,
            payload={'visit_id': visit.id, 'student_id': student.id, 'outcome': outcome},
        )

        if outcome in (ClinicOutcome.SENT_HOME, ClinicOutcome.REFERRED):
            _apply_sakit_override(visit)
            _apply_sakit_override_to_remaining_periods(visit)

    if outcome in (ClinicOutcome.SENT_HOME, ClinicOutcome.REFERRED):
        _dispatch_clinic_incident_notifications(visit)

    return visit


def _get_homeroom_teacher(student: Student):
    from apps.academic.models import ClassEnrollment

    enrollment = ClassEnrollment.objects.filter(
        foundation_id=student.foundation_id,
        student=student,
        is_active=True,
        deleted_at__isnull=True,
    ).select_related('class_group__homeroom_teacher__person', 'class_group__homeroom_teacher__user').first()
    return enrollment.class_group.homeroom_teacher if (enrollment and enrollment.class_group) else None


def _clinic_override_note_and_user(visit: ClinicVisit):
    """Shared by every attendance-override write a clinic visit triggers (day-level and
    per-period), so the note text and acting-user derivation can't drift between them."""
    note_text = f"Klinik: {visit.get_outcome_display()} (kunjungan #{visit.id})"
    user = visit.handled_by.user if visit.handled_by else None
    return note_text, user


def _apply_sakit_override(visit: ClinicVisit) -> None:
    """LIF-003: get-or-create today's AttendanceDay for the visit's student, then reuse the
    shared day-level override service (design doc §6) so the mutation is audited the same
    way as any other staff attendance override."""
    from apps.attendance.models import AttendanceDay, AttendanceSource, AttendanceStatus
    from apps.attendance.services import override_attendance_day

    note_text, user = _clinic_override_note_and_user(visit)
    visit_date = visit.occurred_at.date()

    att_day = AttendanceDay.all_tenants.filter(
        foundation_id=visit.foundation_id,
        school=visit.school,
        student=visit.student,
        date=visit_date,
        deleted_at__isnull=True,
    ).first()

    if not att_day:
        att_day = AttendanceDay.objects.create(
            foundation_id=visit.foundation_id,
            school=visit.school,
            student=visit.student,
            date=visit_date,
            source=AttendanceSource.MANUAL,
        )
    elif att_day.source != AttendanceSource.MANUAL:
        att_day.source = AttendanceSource.MANUAL
        att_day.save(update_fields=['source', 'updated_at'])

    override_attendance_day(
        foundation_id=visit.foundation_id,
        attendance_day=att_day,
        new_status=AttendanceStatus.SAKIT,
        note=note_text,
        user=user,
    )


def _apply_sakit_override_to_remaining_periods(visit: ClinicVisit) -> None:
    """LIF-003: also mark the student's remaining scheduled periods that day SAKIT in
    PeriodAttendance, not just the day-level AttendanceDay override — the DSAR export
    (apps.compliance.services) and the teacher agenda / get_expected_periods_for_school
    ('attendance_submitted') both read PeriodAttendance directly and would otherwise show
    those periods as unrecorded rather than excused.

    'Remaining' = the student's TimetableSlots that weekday whose start_time is still
    ahead of the visit's school-local wall-clock time — the in-progress period at the
    moment of the visit, and every period before it, are left alone entirely. Delegates the
    actual TimetableSlot lookup + get_or_create write to the shared
    apps.attendance.services.mark_period_attendance_for_day (also used by
    approve_absence_request for a whole excused day), so both callers share one
    implementation of "never overwrite an already-recorded period."
    """
    from apps.attendance.models import AttendanceStatus, PeriodAttendanceSource
    from apps.attendance.services import get_school_timezone, mark_period_attendance_for_day

    visit_local = visit.occurred_at.astimezone(get_school_timezone(visit.school))
    note_text, user = _clinic_override_note_and_user(visit)

    created_slot_ids = mark_period_attendance_for_day(
        foundation_id=visit.foundation_id,
        school=visit.school,
        student=visit.student,
        date=visit_local.date(),
        status=AttendanceStatus.SAKIT,
        source=PeriodAttendanceSource.MANUAL,
        note=note_text,
        user=user,
        after_time=visit_local.time(),
    )

    if created_slot_ids:
        audit(
            action='campus.clinic_visit.periods_overridden',
            entity_type='ClinicVisit',
            entity_id=str(visit.id),
            actor_id=str(user.id) if user else None,
            foundation_id=visit.foundation_id,
            school_id=visit.school_id,
            diff={'student_id': visit.student_id, 'date': str(visit_local.date()), 'slot_ids': created_slot_ids},
        )


def _dispatch_clinic_incident_notifications(visit: ClinicVisit) -> int:
    """LIF-003: notify all guardians + homeroom teacher immediately."""
    try:
        from apps.identity.models import GuardianLink
        from apps.notifications.models import NotificationCategory, NotificationPriority
        from apps.notifications.services import dispatch_intent

        student = visit.student
        school = visit.school
        foundation_id = visit.foundation_id

        guardian_links = GuardianLink.objects.filter(
            foundation_id=foundation_id,
            student=student,
            deleted_at__isnull=True,
        ).select_related('guardian__person', 'guardian__user')

        student_name = student.person.full_name if (student.person and student.person.full_name) else (student.nis or 'Siswa')
        school_name = school.name if school else 'Sekolah'
        outcome_label = visit.get_outcome_display()

        recipients = []
        for link in guardian_links:
            guardian = link.guardian
            recipient_name = guardian.person.full_name if (guardian.person and guardian.person.full_name) else 'Wali Murid'
            recipients.append((guardian.user, recipient_name))

        homeroom_teacher = _get_homeroom_teacher(student)
        if homeroom_teacher and homeroom_teacher.user:
            teacher_name = homeroom_teacher.person.full_name if (homeroom_teacher.person and homeroom_teacher.person.full_name) else 'Wali Kelas'
            recipients.append((homeroom_teacher.user, teacher_name))

        dispatched = 0
        for user, recipient_name in recipients:
            if not user:
                continue
            phone = getattr(user, 'phone_e164', '') or ''
            email = getattr(user, 'email', '') or ''
            dedupe_key = f"clinic_incident:{visit.id}:{user.id}"
            payload = {
                'type': NotificationCategory.CLINIC_INCIDENT,
                'student_id': student.id,
                'student_name': student_name,
                'school_name': school_name,
                'outcome': visit.outcome,
                'outcome_label': outcome_label,
                'visit_id': visit.id,
            }
            dispatch_intent(
                foundation_id=foundation_id,
                school_id=school.id,
                recipient_user=user,
                recipient_phone=phone,
                recipient_email=email,
                recipient_name=recipient_name,
                category=NotificationCategory.CLINIC_INCIDENT,
                template_key='clinic.incident',
                payload=payload,
                priority=NotificationPriority.HIGH,
                dedupe_key=dedupe_key,
                immediate=True,
            )
            dispatched += 1

        if dispatched:
            visit.guardian_notified_at = timezone.now()
            visit.save(update_fields=['guardian_notified_at', 'updated_at'])

        return dispatched
    except Exception as exc:
        logger.warning(
            "Failed to dispatch clinic incident notifications for visit %s: %s",
            visit.id,
            exc,
        )
        return 0

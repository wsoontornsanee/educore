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

            stock = MedicationStock.objects.select_for_update().get(pk=medication.pk)
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

    return visit

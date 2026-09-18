import logging

from django.db.models import F, Q
from django.utils import timezone

from apps.identity.models import School, Student
from .models import ClinicPolicy, HealthProfile, MedicationStock

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

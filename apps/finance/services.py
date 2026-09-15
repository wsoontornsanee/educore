import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, record_domain_event
from apps.identity.models import GuardianLink, School, Student, User
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeAssignmentSource,
    FeeCategory,
    FeePlan,
    FeeType,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)
from educore.middleware.tenancy import get_current_foundation_id, tenant_context


def calculate_idr_rounding(amount: Decimal) -> Decimal:
    """
    Calculates the PEMBULATAN adjustment to round IDR total to nearest Rp 100 (FIN-008c, CUR-019).
    
    Example:
    - 750,030 -> -30.00 (Total becomes 750,000)
    - 750,050 -> +50.00 (Total becomes 750,100)
    - 750,075 -> +25.00 (Total becomes 750,100)
    - 750,000 -> 0.00
    """
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    # Modulo on minor units
    rem = amount % Decimal('100.00')
    if rem == Decimal('0.00'):
        return Decimal('0.00')

    if rem < Decimal('50.00'):
        # Round down: subtract remainder
        return -rem
    else:
        # Round up: add difference to 100
        return Decimal('100.00') - rem


def get_student_child_order(student: Student) -> int:
    """
    Determines student's child order among active siblings sharing a primary/financial guardian (FIN-006).
    
    Oldest active child is 1, second is 2, third is 3, etc., sorted by date of birth.
    """
    # 1. Find guardian links for this student
    primary_links = GuardianLink.objects.filter(
        foundation_id=student.foundation_id,
        student=student,
        deleted_at__isnull=True,
    ).filter(models.Q(is_primary=True) | models.Q(financial_responsible=True))

    if not primary_links.exists():
        primary_links = GuardianLink.objects.filter(
            foundation_id=student.foundation_id,
            student=student,
            deleted_at__isnull=True,
        )

    guardian_ids = primary_links.values_list('guardian_id', flat=True)
    if not guardian_ids:
        return 1

    # 2. Find all active students linked to these guardians
    sibling_student_ids = GuardianLink.objects.filter(
        foundation_id=student.foundation_id,
        guardian_id__in=guardian_ids,
        deleted_at__isnull=True,
    ).values_list('student_id', flat=True)

    siblings = Student.objects.filter(
        foundation_id=student.foundation_id,
        id__in=sibling_student_ids,
        status=Student.STATUS_ACTIVE,
        deleted_at__isnull=True,
    ).select_related('person')

    if siblings.count() <= 1:
        return 1

    # Sort siblings by date of birth (oldest first; fallback to creation time)
    sorted_siblings = sorted(
        siblings,
        key=lambda s: (s.person.dob or datetime.date(2099, 1, 1), s.created_at)
    )

    for idx, sib in enumerate(sorted_siblings, start=1):
        if sib.id == student.id:
            return idx

    return 1


def calculate_sibling_discount(
    student: Student,
    fee_type: FeeType,
    base_amount: Decimal,
) -> Decimal:
    """
    Calculates applicable sibling discount for SPP (FIN-006).
    """
    if fee_type.category != FeeCategory.SPP or base_amount <= Decimal('0.00'):
        return Decimal('0.00')

    child_order = get_student_child_order(student)
    if child_order <= 1:
        return Decimal('0.00')

    policy = SiblingDiscountPolicy.objects.filter(
        foundation_id=student.foundation_id,
        school=student.school,
        child_order=child_order,
        fee_category=FeeCategory.SPP,
        is_active=True,
        deleted_at__isnull=True,
    ).first()

    if not policy or policy.discount_percent <= Decimal('0.00'):
        return Decimal('0.00')

    discount_amount = (base_amount * (policy.discount_percent / Decimal('100.00'))).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    return min(discount_amount, base_amount)


@transaction.atomic
def create_discount_with_approval_check(
    foundation_id: int,
    student: Student,
    type: str,
    value: Decimal,
    reason: str,
    valid_from: datetime.date,
    valid_to: Optional[datetime.date] = None,
    fee_type: Optional[FeeType] = None,
    user: Optional[Any] = None,
    approval_threshold: Decimal = Decimal('1000000.00'),
    percent_threshold: Decimal = Decimal('25.00'),
) -> Discount:
    """
    Creates a discount or waiver with automatic foundation approval gating (FIN-007).
    
    Discounts exceeding approval_threshold (default Rp 1,000,000 or >25%) require approval.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    # Validation
    if type == DiscountType.PERCENT and (value <= Decimal('0.00') or value > Decimal('100.00')):
        raise ValidationError(_("Persentase diskon harus antara 0.01% dan 100%."))
    elif type == DiscountType.FIXED and value <= Decimal('0.00'):
        raise ValidationError(_("Nominal diskon harus lebih dari 0.00."))

    needs_approval = False
    if type == DiscountType.PERCENT and value > percent_threshold:
        needs_approval = True
    elif type == DiscountType.FIXED and value > approval_threshold:
        needs_approval = True

    status = DiscountStatus.PENDING_APPROVAL if needs_approval else DiscountStatus.APPROVED
    approved_by = None if needs_approval else (user if user and getattr(user, 'id', None) else None)
    approved_at = None if needs_approval else timezone.now()

    discount = Discount.objects.create(
        foundation_id=foundation_id,
        student=student,
        fee_type=fee_type,
        type=type,
        value=value,
        reason=reason,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        approved_by=approved_by,
        approved_at=approved_at,
    )

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''
    audit(
        action='finance.discount.created',
        entity_type='Discount',
        entity_id=discount.id,
        actor_id=actor_id,
        foundation_id=foundation_id,
        school_id=student.school_id,
        diff={
            'student_id': student.id,
            'type': type,
            'value': str(value),
            'status': status,
            'needs_approval': needs_approval,
        }
    )

    return discount


@transaction.atomic
def approve_discount(discount: Discount, user: Any) -> Discount:
    """Approve a pending discount request (FIN-007)."""
    if discount.status == DiscountStatus.APPROVED:
        return discount

    discount.status = DiscountStatus.APPROVED
    discount.approved_by = user if user and getattr(user, 'id', None) else None
    discount.approved_at = timezone.now()
    discount.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

    audit(
        action='finance.discount.approved',
        entity_type='Discount',
        entity_id=discount.id,
        actor_id=str(user.id) if user and getattr(user, 'id', None) else '',
        foundation_id=discount.foundation_id,
        school_id=discount.student.school_id,
        diff={'status': DiscountStatus.APPROVED}
    )

    return discount


def resolve_student_fee_schedule(
    student: Student,
    period: str,
) -> List[Dict[str, Any]]:
    """
    Resolves the fee schedule for a student in a billing period (YYYY-MM).
    
    Implements 3-tier hierarchical resolution precedence (FIN-001):
    STUDENT assignment > CLASS fee plan > GRADE fee plan.
    """
    foundation_id = student.foundation_id
    school = student.school
    resolved_items: Dict[int, Dict[str, Any]] = {}

    # 1. Tier 1: Check direct StudentFeeAssignment (Individual student override)
    student_assignments = StudentFeeAssignment.objects.filter(
        foundation_id=foundation_id,
        student=student,
        start_period__lte=period,
        is_active=True,
        deleted_at__isnull=True,
    ).filter(
        models.Q(end_period__isnull=True) | models.Q(end_period__gte=period)
    ).select_related('fee_type')

    for assignment in student_assignments:
        fee_type = assignment.fee_type
        if fee_type.is_active:
            resolved_items[fee_type.id] = {
                'fee_type': fee_type,
                'amount': assignment.amount,
                'currency': assignment.currency,
                'source': FeeAssignmentSource.STUDENT,
            }

    # 2. Tier 2: Check FeePlan for student's grade/class
    # Only populate fee types not already overridden by Tier 1
    grade_level_str = getattr(student, 'grade_level', '') or '7'
    fee_plans = FeePlan.objects.filter(
        foundation_id=foundation_id,
        school=school,
        is_active=True,
        deleted_at__isnull=True,
    )

    for plan in fee_plans:
        if not plan.grade_levels or str(grade_level_str) in [str(g) for g in plan.grade_levels]:
            for line in plan.lines:
                ft_id = line.get('fee_type_id')
                if ft_id and ft_id not in resolved_items:
                    fee_type = FeeType.objects.filter(id=ft_id, is_active=True).first()
                    if fee_type:
                        resolved_items[ft_id] = {
                            'fee_type': fee_type,
                            'amount': Decimal(str(line.get('amount', fee_type.default_amount))),
                            'currency': line.get('currency', fee_type.currency),
                            'source': FeeAssignmentSource.GRADE,
                        }

    # 3. Tier 3: School default fee types for active periodic fees
    if not resolved_items:
        default_fee_types = FeeType.objects.filter(
            foundation_id=foundation_id,
            school=school,
            category=FeeCategory.SPP,
            is_active=True,
            deleted_at__isnull=True,
        )
        for ft in default_fee_types:
            resolved_items[ft.id] = {
                'fee_type': ft,
                'amount': ft.default_amount,
                'currency': ft.currency,
                'source': FeeAssignmentSource.GRADE,
            }

    # Calculate discounts per resolved line
    results = []
    period_date = datetime.date.fromisoformat(f"{period}-01")

    for ft_id, item in resolved_items.items():
        fee_type = item['fee_type']
        base_amount = item['amount']

        # Approved explicit discounts
        discounts = Discount.objects.filter(
            foundation_id=foundation_id,
            student=student,
            status=DiscountStatus.APPROVED,
            valid_from__lte=period_date,
            deleted_at__isnull=True,
        ).filter(
            models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=period_date)
        ).filter(
            models.Q(fee_type__isnull=True) | models.Q(fee_type=fee_type)
        )

        discount_total = Decimal('0.00')
        for disc in discounts:
            if disc.type == DiscountType.PERCENT:
                d_val = (base_amount * (disc.value / Decimal('100.00'))).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
            else:
                d_val = disc.value
            discount_total += d_val

        # Sibling discount
        sib_disc = calculate_sibling_discount(student, fee_type, base_amount)
        discount_total += sib_disc

        final_amount = max(Decimal('0.00'), base_amount - discount_total)

        results.append({
            'fee_type_id': fee_type.id,
            'fee_type_code': fee_type.code,
            'fee_type_name': fee_type.name,
            'category': fee_type.category,
            'base_amount': base_amount,
            'discount_amount': min(discount_total, base_amount),
            'final_amount': final_amount,
            'currency': item['currency'],
            'source': item['source'],
        })

    return results

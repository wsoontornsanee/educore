import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, record_domain_event
from apps.identity.models import Foundation, GuardianLink, School, Student, User
from apps.finance.models import (
    Discount,
    DiscountStatus,
    DiscountType,
    FeeAssignmentSource,
    FeeCategory,
    FeePlan,
    FeeRecurrence,
    FeeType,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    InvoiceWriteOffStatus,
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
    approval_threshold: Optional[Decimal] = None,
    percent_threshold: Decimal = Decimal('25.00'),
) -> Discount:
    """
    Creates a discount or waiver with automatic foundation approval gating (FIN-007).

    Discounts exceeding approval_threshold (default: foundation.approval_threshold, FND-007) or >25% require approval.
    """
    if approval_threshold is None:
        approval_threshold = Foundation.objects.get(id=foundation_id).approval_threshold

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
    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

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
        created_by=actor_id,
    )

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
def approve_discount(discount: Discount, user: Any, reason: str = '') -> Discount:
    """Approve a pending discount request (FIN-007, FND-007, FND-008)."""
    # Authority first: even the idempotent "already approved" no-op must not
    # report success to a caller who could never have approved it.
    from apps.identity.rbac import is_foundation_admin
    if not is_foundation_admin(user, discount.foundation_id):
        raise PermissionDenied(_("Persetujuan diskon atau keringanan biaya memerlukan wewenang Admin Yayasan (FIN-007, FND-007)."))

    if discount.status == DiscountStatus.APPROVED:
        return discount
    if discount.status != DiscountStatus.PENDING_APPROVAL:
        raise ValidationError(_("Hanya permohonan diskon dengan status PENDING_APPROVAL yang dapat disetujui."))

    if not reason or not reason.strip():
        raise ValidationError(_("Alasan persetujuan diskon wajib diisi."))

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
        diff={'status': DiscountStatus.APPROVED, 'reason': reason.strip()}
    )

    if discount.type == DiscountType.FIXED:
        _apply_waiver_to_invoice_line(discount, user)

    return discount


def _apply_waiver_to_invoice_line(discount: Discount, user: Any) -> None:
    """Marks the invoice line a FIXED-type discount ("waiver") already
    covers, waived, when one exists (spec/03 FND-007 AC#2: "Approving a
    Rp 5,000,000 waiver marks the underlying invoice line WAIVED and emits
    finance.waiver.approved").

    Scope, matching the read-side waiver/discount split already established
    in apps/foundation/approvals.py's _discount_approval_type: only a
    FIXED-type Discount targets a specific existing line here. A discount
    with no fee_type (applies to every fee type at generation time) has no
    single "underlying invoice line" to point at, so it is left to apply at
    the next invoice generation cycle via resolve_student_fee_schedule
    instead of retroactively guessing which line(s) to touch.

    No-op (no line waived, no event emitted) when the waiver was approved
    before any matching invoice existed yet -- the discount still applies
    forward through resolve_student_fee_schedule once one is generated, or
    when the waiver's valid_from/valid_to window doesn't cover any open
    invoice's period.

    Called from within approve_discount's @transaction.atomic; the invoice
    row is locked with select_for_update so a concurrent payment allocation
    or a second waiver can't read a stale total/paid and clobber this one.
    """
    if not discount.fee_type_id:
        return

    period_filter = models.Q(invoice__period__gte=discount.valid_from.strftime('%Y-%m'))
    if discount.valid_to:
        period_filter &= models.Q(invoice__period__lte=discount.valid_to.strftime('%Y-%m'))

    candidate = InvoiceLine.objects.filter(
        foundation_id=discount.foundation_id,
        invoice__student=discount.student,
        invoice__status__in=[InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
        invoice__deleted_at__isnull=True,
        fee_type_id=discount.fee_type_id,
        subtotal__gt=Decimal('0.00'),
        deleted_at__isnull=True,
    ).filter(period_filter).order_by('invoice__period').first()
    if not candidate:
        return

    # Re-fetch under a row lock: candidate's invoice/period selection above
    # doesn't need to be repeated once locked, since nothing else waives
    # lines (only payments and this same function mutate invoice.paid/total,
    # both take out this lock).
    with transaction.atomic():
        line = InvoiceLine.objects.select_for_update().select_related('invoice').get(id=candidate.id)
        invoice = Invoice.objects.select_for_update().get(id=line.invoice_id)

        waive_amount = min(discount.value, line.subtotal)
        if waive_amount <= Decimal('0.00'):
            return

        line.discount += waive_amount
        line.subtotal = max(Decimal('0.00'), line.subtotal - waive_amount)
        line.waived = line.subtotal <= Decimal('0.00')
        line.save(update_fields=['waived', 'discount', 'subtotal', 'updated_at'])

        invoice.discount += waive_amount
        invoice.total = max(Decimal('0.00'), invoice.total - waive_amount)
        if invoice.status != InvoiceStatus.DRAFT and invoice.paid >= invoice.total:
            invoice.status = InvoiceStatus.PAID
        invoice.save(update_fields=['discount', 'total', 'status', 'updated_at'])

        from apps.finance.services.ledger import post_waiver_journal
        journal = post_waiver_journal(invoice=invoice, amount=waive_amount, reason=discount.reason)

        audit(
            action='finance.waiver.approved',
            entity_type='InvoiceLine',
            entity_id=line.id,
            actor_id=str(user.id) if user and getattr(user, 'id', None) else '',
            foundation_id=discount.foundation_id,
            school_id=invoice.school_id,
            diff={
                'discount_id': discount.id,
                'invoice_id': invoice.id,
                'waived_amount': str(waive_amount),
                'journal_id': journal.id,
            },
        )


@transaction.atomic
def reject_discount(discount: Discount, user: Any, reason: str = '') -> Discount:
    """Reject a pending discount request (FIN-007, FND-007, FND-008)."""
    if discount.status != DiscountStatus.PENDING_APPROVAL:
        raise ValidationError(_("Hanya permohonan diskon dengan status PENDING_APPROVAL yang dapat ditolak."))

    from apps.identity.rbac import is_foundation_admin
    if not is_foundation_admin(user, discount.foundation_id):
        raise PermissionDenied(_("Penolakan permohonan diskon memerlukan wewenang Admin Yayasan (FIN-007, FND-007)."))

    if not reason or not reason.strip():
        raise ValidationError(_("Alasan penolakan diskon wajib diisi."))

    discount.status = DiscountStatus.REJECTED
    discount.save(update_fields=['status', 'updated_at'])

    audit(
        action='finance.discount.rejected',
        entity_type='Discount',
        entity_id=discount.id,
        actor_id=str(user.id) if user and getattr(user, 'id', None) else '',
        foundation_id=discount.foundation_id,
        school_id=discount.student.school_id,
        diff={'status': DiscountStatus.REJECTED, 'reason': reason.strip()}
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

    # 3. Tier 3: School default fee types for active periodic fees (SPP and recurring monthly fees)
    if not resolved_items:
        default_fee_types = FeeType.objects.filter(
            foundation_id=foundation_id,
            school=school,
            recurrence=FeeRecurrence.MONTHLY,
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


def generate_invoice_number(school: School, year: int) -> str:
    """
    Allocates an atomic, gapless sequential invoice number per school per year (FIN-004).
    Format: INV/{school_code}/{YYYY}/{NNNNNN}
    
    Must be called within an active database transaction.
    """
    foundation_id = school.foundation_id
    # Clean alphanumeric school code from npsn or sanitized name
    school_code = school.npsn if school.npsn else f"SCH{school.id}"

    seq, _ = InvoiceNumberSequence.all_tenants.select_for_update().get_or_create(
        foundation_id=foundation_id,
        school=school,
        year=year,
        defaults={'last_number': 0}
    )
    seq.last_number += 1
    seq.save(update_fields=['last_number', 'updated_at'])

    return f"INV/{school_code}/{year}/{seq.last_number:06d}"


def generate_monthly_invoices(
    school: School,
    period: str,
    issue_date: Optional[datetime.date] = None,
    due_date: Optional[datetime.date] = None,
    dry_run: bool = False,
    triggered_by: Optional[User] = None,
) -> Dict[str, Any]:
    """
    Monthly SPP and fee invoice generation engine (spec/06 §3, FIN-002, FIN-003, FIN-004, FIN-005, FIN-008, FIN-008c).
    
    - Idempotent: re-running for the same (student, period) skips existing invoices (FIN-003).
    - Filters only ACTIVE students (FIN-005).
    - Formats sequential numbers per school per year (FIN-004).
    - Enforces IDR PEMBULATAN rounding to Rp 100 (FIN-008c, CUR-019).
    - Dispatches notifications to guardians on commit.
    - Dry-run mode produces full preview without persisting to DB (FIN-008).
    """
    foundation_id = school.foundation_id
    if not issue_date:
        issue_date = timezone.localdate()
    if not due_date:
        # Default due date: 10th of the billing period month
        year_str, month_str = period.split('-')
        due_date = datetime.date(int(year_str), int(month_str), 10)

    # 1. Fetch active students in school
    students = Student.objects.filter(
        foundation_id=foundation_id,
        school=school,
        status=Student.STATUS_ACTIVE,
        deleted_at__isnull=True,
    ).select_related('person').order_by('id')

    # Also track excluded students count for report
    excluded_students = Student.objects.filter(
        foundation_id=foundation_id,
        school=school,
        deleted_at__isnull=True,
    ).exclude(status=Student.STATUS_ACTIVE)

    exclusions: List[Dict[str, Any]] = [
        {
            'student_id': s.id,
            'name': s.person.full_name,
            'nis': s.nis,
            'status': s.status,
            'reason': _("Status bukan ACTIVE (FIN-005)"),
        }
        for s in excluded_students
    ]

    report = {
        'school_id': school.id,
        'school_name': school.name,
        'period': period,
        'dry_run': dry_run,
        'total_active_students': students.count(),
        'generated_count': 0,
        'skipped_existing_count': 0,
        'total_billed_amount': Decimal('0.00'),
        'total_discount_amount': Decimal('0.00'),
        'total_rounding_amount': Decimal('0.00'),
        'total_net_amount': Decimal('0.00'),
        'currency': school.base_currency or 'IDR',
        'exclusions': exclusions,
        'invoices': [],
    }

    year_int = int(period.split('-')[0])

    for student in students:
        # Idempotency check: already invoiced for this period? (FIN-003)
        existing = Invoice.objects.filter(
            foundation_id=foundation_id,
            student=student,
            period=period,
            deleted_at__isnull=True,
        ).first()

        if existing:
            report['skipped_existing_count'] += 1
            continue

        # Resolve student fee schedule (FIN-001)
        schedule_items = resolve_student_fee_schedule(student, period)
        if not schedule_items:
            continue

        # Aggregate line items
        subtotal = sum((item['base_amount'] for item in schedule_items), Decimal('0.00'))
        discount_total = sum((item['discount_amount'] for item in schedule_items), Decimal('0.00'))
        net_before_rounding = subtotal - discount_total

        # IDR PEMBULATAN rounding line item (FIN-008c, CUR-019)
        rounding = Decimal('0.00')
        if (school.base_currency or 'IDR') == 'IDR':
            rounding = calculate_idr_rounding(net_before_rounding)

        final_total = max(Decimal('0.00'), net_before_rounding + rounding)

        preview_data = {
            'student_id': student.id,
            'student_name': student.person.full_name,
            'nis': student.nis,
            'subtotal': str(subtotal),
            'discount': str(discount_total),
            'rounding': str(rounding),
            'total': str(final_total),
            'currency': school.base_currency or 'IDR',
            'lines_count': len(schedule_items) + (1 if rounding != Decimal('0.00') else 0),
        }

        if dry_run:
            report['generated_count'] += 1
            report['total_billed_amount'] += subtotal
            report['total_discount_amount'] += discount_total
            report['total_rounding_amount'] += rounding
            report['total_net_amount'] += final_total
            report['invoices'].append(preview_data)
            continue

        # Commit generation in transaction per invoice
        with transaction.atomic():
            invoice_num = generate_invoice_number(school, year_int)
            invoice = Invoice.objects.create(
                foundation_id=foundation_id,
                school=school,
                student=student,
                number=invoice_num,
                period=period,
                issue_date=issue_date,
                due_date=due_date,
                subtotal=subtotal,
                discount=discount_total,
                rounding=rounding,
                total=final_total,
                paid=Decimal('0.00'),
                currency=school.base_currency or 'IDR',
                status=InvoiceStatus.ISSUED,
                created_by=str(triggered_by.id) if triggered_by else 'system',
            )

            # Persist fee lines
            for item in schedule_items:
                InvoiceLine.objects.create(
                    foundation_id=foundation_id,
                    invoice=invoice,
                    fee_type_id=item['fee_type_id'],
                    code=item['fee_type_code'],
                    description=item['fee_type_name'],
                    amount=item['base_amount'],
                    discount=item['discount_amount'],
                    subtotal=item['final_amount'],
                    currency=item['currency'],
                )

            # Persist PEMBULATAN line if applicable
            if rounding != Decimal('0.00'):
                InvoiceLine.objects.create(
                    foundation_id=foundation_id,
                    invoice=invoice,
                    fee_type=None,
                    code='PEMBULATAN',
                    description=_("Pembulatan ke ratusan terdekat"),
                    amount=rounding,
                    discount=Decimal('0.00'),
                    subtotal=rounding,
                    currency=school.base_currency or 'IDR',
                )

            # Audit event & domain event
            audit(
                action='finance.invoice.generated',
                entity_type='Invoice',
                entity_id=invoice.id,
                actor_id=str(triggered_by.id) if triggered_by else 'system',
                role='system' if not triggered_by else 'finance_officer',
                foundation_id=foundation_id,
                school_id=school.id,
                diff={'invoice_number': invoice.number, 'total': str(invoice.total), 'period': period},
            )
            record_domain_event(
                name='finance.invoice.issued',
                payload={
                    'invoice_id': invoice.id,
                    'number': invoice.number,
                    'student_id': student.id,
                    'total': str(invoice.total),
                    'currency': invoice.currency,
                    'period': period,
                    'due_date': str(due_date),
                    'school_id': school.id,
                },
                foundation_id=foundation_id,
            )

            # Enqueue notification intent for primary/financial guardian
            _dispatch_invoice_notification(invoice, student)

            preview_data['invoice_number'] = invoice.number
            report['generated_count'] += 1
            report['total_billed_amount'] += subtotal
            report['total_discount_amount'] += discount_total
            report['total_rounding_amount'] += rounding
            report['total_net_amount'] += final_total
            report['invoices'].append(preview_data)

    # Convert totals to string for JSON serialization
    report['total_billed_amount'] = str(report['total_billed_amount'])
    report['total_discount_amount'] = str(report['total_discount_amount'])
    report['total_rounding_amount'] = str(report['total_rounding_amount'])
    report['total_net_amount'] = str(report['total_net_amount'])

    return report


def _dispatch_invoice_notification(invoice: Invoice, student: Student):
    """Dispatches invoice issuance WhatsApp/push intent to primary/financial guardian."""
    try:
        from apps.notifications.services import create_notification_intent
        from apps.notifications.models import NotificationCategory, NotificationPriority

        guardian_link = GuardianLink.objects.filter(
            foundation_id=invoice.foundation_id,
            student=student,
            deleted_at__isnull=True,
        ).filter(models.Q(is_primary=True) | models.Q(financial_responsible=True)).select_related('guardian__user', 'guardian__person').first()

        if guardian_link and guardian_link.guardian:
            guardian = guardian_link.guardian
            recipient_phone = guardian.user.phone_e164 if guardian.user else ""
            recipient_name = guardian.person.full_name if guardian.person else ""

            create_notification_intent(
                foundation_id=invoice.foundation_id,
                school_id=invoice.school_id,
                recipient_user=guardian.user,
                recipient_phone=recipient_phone,
                recipient_name=recipient_name,
                category=NotificationCategory.FINANCE_BILLING,
                template_key='invoice.issued',
                payload={
                    'student_name': student.person.full_name,
                    'invoice_number': invoice.number,
                    'period': invoice.period,
                    'total': str(invoice.total),
                    'currency': invoice.currency,
                    'due_date': str(invoice.due_date),
                },
                priority=NotificationPriority.NORMAL,
                dedupe_key=f"inv_notif_{invoice.id}",
            )
    except Exception as e:
        # Non-blocking notification dispatch failure
        pass


def cancel_invoice(invoice: Invoice, user: User, reason: str = "") -> Invoice:
    """Cancels an unpaid or draft invoice (spec/06 §3, FIN-009)."""
    if invoice.status in [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF]:
        raise ValidationError(_("Faktur yang sudah dibayar, dibatalkan, atau dihapusbuku tidak dapat dibatalkan."))
    if invoice.paid > Decimal('0.00'):
        raise ValidationError(_("Faktur yang sudah memiliki pembayaran parsial tidak dapat dibatalkan langsung."))

    old_status = invoice.status
    invoice.status = InvoiceStatus.CANCELLED
    invoice.updated_by = str(user.id)
    invoice.save(update_fields=['status', 'updated_by', 'updated_at'])

    audit(
        action='finance.invoice.cancelled',
        entity_type='Invoice',
        entity_id=invoice.id,
        actor_id=str(user.id),
        role='finance_officer',
        foundation_id=invoice.foundation_id,
        school_id=invoice.school_id,
        diff={'before': old_status, 'after': invoice.status, 'reason': reason},
    )

    return invoice


def request_invoice_write_off(
    invoice: Invoice,
    user: User,
    reason: str,
    amount: Optional[Decimal] = None,
) -> InvoiceWriteOffRequest:
    """Submits an invoice write-off request for foundation approval (spec/06 §6, FIN-031)."""
    if invoice.status in [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF]:
        raise ValidationError(_("Faktur dengan status ini tidak dapat diajukan untuk penghapusbukuan."))

    if invoice.balance_due <= Decimal('0.00'):
        raise ValidationError(_("Faktur tidak memiliki saldo piutang tertunggak."))

    write_off_amount = amount if amount is not None else invoice.balance_due
    if write_off_amount <= Decimal('0.00') or write_off_amount > invoice.balance_due:
        raise ValidationError(_("Nominal penghapusbukuan tidak valid atau melebihi sisa tagihan."))

    if InvoiceWriteOffRequest.objects.filter(
        invoice=invoice,
        status=InvoiceWriteOffStatus.PENDING,
        deleted_at__isnull=True,
    ).exists():
        raise ValidationError(_("Permohonan penghapusbukuan untuk faktur ini sedang menunggu persetujuan."))

    request_obj = InvoiceWriteOffRequest.objects.create(
        foundation_id=invoice.foundation_id,
        invoice=invoice,
        school=invoice.school,
        amount=write_off_amount,
        currency=invoice.currency,
        reason=reason,
        status=InvoiceWriteOffStatus.PENDING,
        requested_by=user,
    )

    audit(
        action='finance.invoice.write_off_requested',
        entity_type='InvoiceWriteOffRequest',
        entity_id=request_obj.id,
        actor_id=str(user.id),
        role='finance_officer',
        foundation_id=invoice.foundation_id,
        school_id=invoice.school_id,
        diff={'invoice_id': invoice.id, 'amount': str(write_off_amount), 'reason': reason},
    )

    return request_obj


def approve_invoice_write_off(
    request_obj: InvoiceWriteOffRequest,
    user: User,
    notes: str = "",
) -> InvoiceWriteOffRequest:
    """Approves a bad debt write-off, transitions invoice to WRITTEN_OFF, and posts ledger journal (FIN-031)."""
    if request_obj.status != InvoiceWriteOffStatus.PENDING:
        raise ValidationError(_("Permohonan penghapusbukuan sudah diproses sebelumnya."))

    from apps.identity.rbac import is_foundation_admin
    if not is_foundation_admin(user, request_obj.foundation_id):
        raise PermissionDenied(_("Persetujuan penghapusbukuan piutang memerlukan wewenang Yayasan (FIN-031)."))

    invoice = request_obj.invoice
    with transaction.atomic():
        old_status = invoice.status
        invoice.status = InvoiceStatus.WRITTEN_OFF
        invoice.updated_by = str(user.id)
        invoice.save(update_fields=['status', 'updated_by', 'updated_at'])

        # Post Dr Bad Debt / Cr AR (FIN-031)
        from apps.finance.services.ledger import post_write_off_journal
        journal = post_write_off_journal(
            invoice=invoice,
            amount=request_obj.amount,
            user=user,
            reason=request_obj.reason,
        )

        request_obj.status = InvoiceWriteOffStatus.APPROVED
        request_obj.approved_by = user
        request_obj.resolved_at = timezone.now()
        request_obj.journal = journal
        request_obj.updated_by = str(user.id)
        request_obj.save(update_fields=['status', 'approved_by', 'resolved_at', 'journal', 'updated_by', 'updated_at'])

        audit(
            action='finance.invoice.written_off',
            entity_type='Invoice',
            entity_id=invoice.id,
            actor_id=str(user.id),
            role='foundation_admin',
            foundation_id=invoice.foundation_id,
            school_id=invoice.school_id,
            diff={'before': old_status, 'after': invoice.status, 'amount': str(request_obj.amount), 'reason': request_obj.reason},
        )

    return request_obj


def reject_invoice_write_off(
    request_obj: InvoiceWriteOffRequest,
    user: User,
    reason: str = "",
    notes: str = "",
) -> InvoiceWriteOffRequest:
    """Rejects an invoice write-off request."""
    if request_obj.status != InvoiceWriteOffStatus.PENDING:
        raise ValidationError(_("Permohonan penghapusbukuan sudah diproses sebelumnya."))

    from apps.identity.rbac import is_foundation_admin
    if not is_foundation_admin(user, request_obj.foundation_id):
        raise PermissionDenied(_("Penolakan penghapusbukuan piutang memerlukan wewenang Yayasan (FIN-031)."))

    rejection_note = notes or reason
    request_obj.status = InvoiceWriteOffStatus.REJECTED
    request_obj.rejection_reason = rejection_note
    request_obj.resolved_at = timezone.now()
    request_obj.updated_by = str(user.id)
    request_obj.save(update_fields=['status', 'rejection_reason', 'resolved_at', 'updated_by', 'updated_at'])

    audit(
        action='finance.invoice.write_off_rejected',
        entity_type='InvoiceWriteOffRequest',
        entity_id=request_obj.id,
        actor_id=str(user.id),
        role='foundation_admin',
        foundation_id=request_obj.foundation_id,
        school_id=request_obj.school_id,
        diff={'reason': rejection_note},
    )

    return request_obj


@transaction.atomic
def write_off_invoice(invoice: Invoice, user: User, reason: str = "") -> Invoice:
    """Writes off an overdue invoice as bad debt (spec/06 §3, FIN-009, FIN-031).
    Creates and approves the write-off request and posts the Dr Bad Debt / Cr AR ledger journal.
    Atomic: a caller without foundation authority (PermissionDenied) leaves no
    dangling PENDING request behind.
    """
    req = request_invoice_write_off(invoice, user, reason)
    approve_invoice_write_off(req, user)
    invoice.refresh_from_db()
    return invoice



@transaction.atomic
def add_adhoc_invoice_line(student: Student, school: School, code: str, description: str, amount: Decimal) -> Invoice:
    """Appends a one-off charge to the student's most recent open invoice, or creates a
    minimal new one if none is open. Used for handovers into billing from outside the
    normal monthly generation cycle (spec/17 REC-013: wallet reconciliation handover)."""
    foundation_id = student.foundation_id
    currency = school.base_currency or 'IDR'

    invoice = Invoice.objects.filter(
        foundation_id=foundation_id, student=student,
        status__in=[InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
        deleted_at__isnull=True,
    ).order_by('-period').first()

    if not invoice:
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            foundation_id=foundation_id,
            school=school,
            student=student,
            number=generate_invoice_number(school, today.year),
            period=today.strftime('%Y-%m'),
            issue_date=today,
            due_date=today + datetime.timedelta(days=7),
            subtotal=Decimal('0.00'),
            discount=Decimal('0.00'),
            rounding=Decimal('0.00'),
            total=Decimal('0.00'),
            paid=Decimal('0.00'),
            currency=currency,
            status=InvoiceStatus.ISSUED,
            created_by='system',
        )

    InvoiceLine.objects.create(
        foundation_id=foundation_id,
        invoice=invoice,
        fee_type=None,
        code=code,
        description=description,
        amount=amount,
        discount=Decimal('0.00'),
        subtotal=amount,
        currency=currency,
    )
    invoice.subtotal += amount
    invoice.total += amount
    invoice.save(update_fields=['subtotal', 'total', 'updated_at'])

    audit(
        action='finance.invoice.adhoc_line_added',
        entity_type='Invoice',
        entity_id=invoice.id,
        foundation_id=foundation_id,
        school_id=school.id,
        diff={'code': code, 'amount': str(amount)},
    )
    return invoice

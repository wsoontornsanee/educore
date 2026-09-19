import datetime
import logging
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Union
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import audit, record_domain_event
from apps.finance.models import (
    Invoice,
    InvoiceInstallment,
    InvoiceInstallmentStatus,
    InvoiceStatus,
)

logger = logging.getLogger(__name__)


class InvalidInstallmentError(ValueError):
    pass


class InstallmentPlanAlreadyExistsError(InvalidInstallmentError):
    pass


def calculate_installments_largest_remainder(
    total_amount: Decimal,
    count: int,
    first_due_date: datetime.date,
    interval_days: int = 30,
    currency: str = 'IDR',
) -> List[Dict[str, Any]]:
    """
    Split total_amount into `count` dated installments using the largest-remainder method (CUR-018, CUR-019).
    
    Requirements:
    - CUR-018: Components must sum exactly to the total. Residual cent/rupiah goes to the first line, deterministically.
    - CUR-019: For IDR, installment amounts round to Rp 100.
    - Non-IDR: Installment amounts round to 0.01.
    - Acceptance criteria #3 (spec/16): Splitting 1000000.00 into 3 installments produces 
      333400.00 + 333300.00 + 333300.00 and sums exactly to the total.
    """
    if count < 1:
        raise InvalidInstallmentError(_("Jumlah cicilan minimal 1."))

    if total_amount <= Decimal('0.00'):
        raise InvalidInstallmentError(_("Total tagihan yang dicicil harus lebih besar dari 0."))

    currency_clean = (currency or 'IDR').upper()
    rounding_unit = Decimal('100.00') if currency_clean == 'IDR' else Decimal('0.01')

    # Compute base unit count per installment
    total_units = int(total_amount // rounding_unit)
    base_units_per_part = total_units // count
    remainder_units = total_units % count
    
    # Leftover unrounded amount (if total was not an exact multiple of rounding_unit)
    leftover_raw = total_amount - (Decimal(total_units) * rounding_unit)

    installments = []
    current_date = first_due_date

    for i in range(count):
        installment_no = i + 1
        # Largest remainder allocation: remainder units distributed to the first lines
        units = base_units_per_part + (1 if i < remainder_units else 0)
        part_amount = (Decimal(units) * rounding_unit).quantize(Decimal('0.01'))

        # Any sub-unit residual deterministically goes to the first line
        if i == 0 and leftover_raw > Decimal('0.00'):
            part_amount += leftover_raw

        installments.append({
            'installment_no': installment_no,
            'due_date': current_date,
            'amount': part_amount,
            'currency': currency_clean,
        })
        current_date = current_date + datetime.timedelta(days=interval_days)

    return installments


@transaction.atomic
def create_installment_plan(
    invoice: Invoice,
    count: Optional[int] = None,
    schedule: Optional[List[Dict[str, Any]]] = None,
    first_due_date: Optional[datetime.date] = None,
    interval_days: int = 30,
    user=None,
    notes: str = '',
) -> List[InvoiceInstallment]:
    """
    Create a payment installment plan for an invoice (FIN-030).
    
    Supports:
    1. Auto-generation: `count` (+ optional `first_due_date`, `interval_days`).
    2. Manual schedule: `schedule` containing `[{'due_date': ..., 'amount': ...}]`.
    """
    if invoice.status not in [InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID]:
        raise InvalidInstallmentError(
            _("Rencana cicilan hanya dapat dibuat untuk tagihan berstatus Diterbitkan atau Dibayar Sebagian.")
        )

    # Check for active installments
    active_installments = invoice.installments.filter(
        deleted_at__isnull=True
    ).exclude(status=InvoiceInstallmentStatus.CANCELLED)

    if active_installments.exists():
        raise InstallmentPlanAlreadyExistsError(
            _("Tagihan sudah memiliki rencana cicilan aktif. Batalkan terlebih dahulu sebelum membuat yang baru.")
        )

    target_amount = invoice.balance_due
    if target_amount <= Decimal('0.00'):
        raise InvalidInstallmentError(_("Sisa tagihan (balance due) adalah 0."))

    parsed_schedule: List[Dict[str, Any]] = []

    if schedule:
        if not isinstance(schedule, list) or len(schedule) == 0:
            raise InvalidInstallmentError(_("Jadwal cicilan tidak boleh kosong."))

        total_scheduled = Decimal('0.00')
        for i, item in enumerate(schedule):
            amt = Decimal(str(item.get('amount', '0.00'))).quantize(Decimal('0.01'))
            if amt <= Decimal('0.00'):
                raise InvalidInstallmentError(_("Nominal cicilan #%(no)s harus lebih besar dari 0.") % {'no': i + 1})

            due_date = item.get('due_date')
            if isinstance(due_date, str):
                try:
                    due_date = datetime.date.fromisoformat(due_date)
                except ValueError:
                    raise InvalidInstallmentError(_("Format tanggal cicilan #%(no)s tidak valid (YYYY-MM-DD).") % {'no': i + 1})
            elif not isinstance(due_date, datetime.date):
                raise InvalidInstallmentError(_("Tanggal cicilan #%(no)s wajib diisi.") % {'no': i + 1})

            if due_date < invoice.issue_date:
                raise InvalidInstallmentError(
                    _("Jatuh tempo cicilan #%(no)s tidak boleh sebelum tanggal penerbitan tagihan (%(issue_date)s).")
                    % {'no': i + 1, 'issue_date': invoice.issue_date}
                )

            total_scheduled += amt
            parsed_schedule.append({
                'installment_no': i + 1,
                'due_date': due_date,
                'amount': amt,
                'currency': invoice.currency,
            })

        if total_scheduled != target_amount:
            raise InvalidInstallmentError(
                _("Total rencana cicilan (%(scheduled)s) tidak sama dengan sisa tagihan (%(target)s).")
                % {'scheduled': total_scheduled, 'target': target_amount}
            )
    elif count:
        if count < 1:
            raise InvalidInstallmentError(_("Jumlah cicilan minimal 1."))

        if first_due_date is None:
            # Default to invoice due date if in the future, else today + 30 days
            today = timezone.localdate()
            first_due_date = invoice.due_date if invoice.due_date >= today else today + datetime.timedelta(days=interval_days)

        parsed_schedule = calculate_installments_largest_remainder(
            total_amount=target_amount,
            count=count,
            first_due_date=first_due_date,
            interval_days=interval_days,
            currency=invoice.currency,
        )
    else:
        raise InvalidInstallmentError(_("Harap tentukan jumlah cicilan (count) atau daftar jadwal (schedule)."))

    created_instances = []
    for item in parsed_schedule:
        inst = InvoiceInstallment.objects.create(
            foundation_id=invoice.foundation_id,
            invoice=invoice,
            installment_no=item['installment_no'],
            due_date=item['due_date'],
            amount=item['amount'],
            paid_amount=Decimal('0.00'),
            currency=invoice.currency,
            status=InvoiceInstallmentStatus.PENDING,
            notes=notes,
        )
        created_instances.append(inst)

    record_domain_event(
        name='finance.installment_plan_created',
        payload={
            'invoice_id': invoice.id,
            'invoice_number': invoice.number,
            'student_id': invoice.student_id,
            'installments_count': len(created_instances),
            'total_amount': str(target_amount),
            'currency': invoice.currency,
        },
        foundation_id=invoice.foundation_id,
    )

    actor_id = str(user.id) if user and hasattr(user, 'id') else None
    audit(
        action='finance.installment_plan.created',
        entity_type='Invoice',
        entity_id=invoice.id,
        foundation_id=invoice.foundation_id,
        school_id=invoice.school_id,
        actor_id=actor_id,
        diff={
            'installments_count': len(created_instances),
            'total_amount': str(target_amount),
            'currency': invoice.currency,
            'schedule': [
                {'no': inst.installment_no, 'due_date': str(inst.due_date), 'amount': str(inst.amount)}
                for inst in created_instances
            ],
        },
    )

    return created_instances


@transaction.atomic
def cancel_installment_plan(invoice: Invoice, user=None, reason: str = '') -> List[InvoiceInstallment]:
    """
    Cancel unpaid installments on an invoice (FIN-030).
    Preserves historical paid/partially-paid lines while marking pending installments as CANCELLED.
    """
    installments = list(
        invoice.installments.select_for_update().filter(
            deleted_at__isnull=True
        ).exclude(status=InvoiceInstallmentStatus.CANCELLED)
    )

    if not installments:
        raise InvalidInstallmentError(_("Tidak ada rencana cicilan aktif untuk tagihan ini."))

    cancelled_count = 0

    for inst in installments:
        if inst.status == InvoiceInstallmentStatus.PENDING:
            inst.status = InvoiceInstallmentStatus.CANCELLED
            inst.notes = f"{inst.notes} | Dibatalkan: {reason}".strip(' |')
            inst.save(update_fields=['status', 'notes', 'updated_at'])
            cancelled_count += 1
        elif inst.status == InvoiceInstallmentStatus.PARTIALLY_PAID:
            inst.status = InvoiceInstallmentStatus.CANCELLED
            inst.notes = f"{inst.notes} | Dibatalkan sebagian: {reason}".strip(' |')
            inst.save(update_fields=['status', 'notes', 'updated_at'])
            cancelled_count += 1

    record_domain_event(
        name='finance.installment_plan_cancelled',
        payload={
            'invoice_id': invoice.id,
            'invoice_number': invoice.number,
            'student_id': invoice.student_id,
            'cancelled_count': cancelled_count,
            'reason': reason,
        },
        foundation_id=invoice.foundation_id,
    )

    actor_id = str(user.id) if user and hasattr(user, 'id') else None
    audit(
        action='finance.installment_plan.cancelled',
        entity_type='Invoice',
        entity_id=invoice.id,
        foundation_id=invoice.foundation_id,
        school_id=invoice.school_id,
        actor_id=actor_id,
        diff={
            'cancelled_count': cancelled_count,
            'reason': reason,
        },
    )

    return installments


@transaction.atomic
def allocate_payment_to_installments(
    invoice: Invoice,
    payment_amount: Decimal,
    paid_at: Optional[datetime.datetime] = None,
) -> Decimal:
    """
    Cascade payment amount oldest-first across pending installments (FIN-030).
    Returns unallocated remainder (if payment exceeds total unpaid installments).
    """
    if payment_amount <= Decimal('0.00'):
        return Decimal('0.00')

    if paid_at is None:
        paid_at = timezone.now()

    unpaid_installments = list(
        invoice.installments.select_for_update().filter(
            status__in=[InvoiceInstallmentStatus.PENDING, InvoiceInstallmentStatus.PARTIALLY_PAID],
            deleted_at__isnull=True,
        ).order_by('due_date', 'installment_no')
    )

    remaining = payment_amount

    for inst in unpaid_installments:
        if remaining <= Decimal('0.00'):
            break

        balance_due = inst.balance_due
        if balance_due <= Decimal('0.00'):
            continue

        alloc = min(remaining, balance_due)
        inst.paid_amount += alloc

        if inst.paid_amount >= inst.amount:
            inst.status = InvoiceInstallmentStatus.PAID
            inst.paid_at = paid_at
        else:
            inst.status = InvoiceInstallmentStatus.PARTIALLY_PAID

        inst.save(update_fields=['paid_amount', 'status', 'paid_at', 'updated_at'])
        remaining -= alloc

    return remaining

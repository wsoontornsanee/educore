import calendar
from datetime import date, datetime, time
from decimal import Decimal
import re
from typing import Optional, Tuple
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.identity.models import School, User
from apps.finance.models import (
    FiscalPeriod,
    FiscalPeriodStatus,
    LedgerEntry,
    LedgerJournal,
)


class PeriodClosedError(ValueError):
    """Raised when an operation attempts to write to a closed fiscal period (FIN-025)."""
    pass


class PeriodCloseValidationError(ValueError):
    """Raised when validation fails during period closing (e.g. unbalanced ledger)."""
    pass


_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def validate_period_format(period: str) -> None:
    """Validate that period conforms to YYYY-MM format."""
    if not period or not isinstance(period, str) or not _PERIOD_RE.match(period):
        raise ValueError(_("Format periode tidak valid. Gunakan format YYYY-MM (contoh: 2026-08)."))


def get_period_date_range(period: str) -> Tuple[date, date]:
    """Return (start_date, end_date) for a given YYYY-MM period."""
    validate_period_format(period)
    year, month = int(period[:4]), int(period[5:7])
    num_days = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, num_days)


def get_next_period_str(period: str) -> str:
    """Return next sequential period string YYYY-MM."""
    validate_period_format(period)
    year, month = int(period[:4]), int(period[5:7])
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def is_period_closed(school: School, period: str) -> bool:
    """Check if the given fiscal period is closed for the school (FIN-025)."""
    validate_period_format(period)
    return FiscalPeriod.all_tenants.filter(
        foundation_id=school.foundation_id,
        school=school,
        period=period,
        status=FiscalPeriodStatus.CLOSED,
        deleted_at__isnull=True,
    ).exists()


def get_next_open_period(school: School, from_period: str) -> str:
    """Find the next open sequential fiscal period for the school starting after from_period."""
    curr = from_period
    # Check up to 24 future months
    for _ in range(24):
        curr = get_next_period_str(curr)
        if not is_period_closed(school, curr):
            return curr
    return curr


def get_effective_posting_datetime(
    school: School,
    target_datetime: Optional[datetime] = None,
    allow_routing: bool = False,
) -> datetime:
    """
    Check if target_datetime falls into a closed fiscal period.
    If closed:
      - If allow_routing=True (FIN-025 post-close corrections), forwards to the start of the next open period.
      - If allow_routing=False, raises PeriodClosedError.
    """
    if target_datetime is None:
        target_datetime = timezone.now()

    period = target_datetime.strftime('%Y-%m')
    if not is_period_closed(school, period):
        return target_datetime

    if not allow_routing:
        raise PeriodClosedError(
            f"Periode fiskal {period} telah ditutup. Entri buku besar tidak dapat dicatat pada periode tertutup."
        )

    next_period = get_next_open_period(school, period)
    start_date, _ = get_period_date_range(next_period)
    routed_dt = datetime.combine(start_date, time(0, 0, 0))
    if timezone.is_aware(target_datetime):
        routed_dt = timezone.make_aware(routed_dt, timezone=timezone.get_current_timezone())
    return routed_dt


@transaction.atomic
def close_fiscal_period(
    school: School,
    period: str,
    closed_by: Optional[User] = None,
    notes: str = '',
) -> FiscalPeriod:
    """
    Close a monthly fiscal period for a school (spec/06 §5, §8, FIN-025).
    Locks the period against further ledger modifications, validates double-entry balance,
    and stores snapshot statistics.
    """
    validate_period_format(period)
    foundation_id = school.foundation_id

    # Check if already closed
    existing = FiscalPeriod.all_tenants.filter(
        foundation_id=foundation_id,
        school=school,
        period=period,
        deleted_at__isnull=True,
    ).first()

    if existing and existing.status == FiscalPeriodStatus.CLOSED:
        return existing

    # Compute period date bounds
    start_date, end_date = get_period_date_range(period)
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

    # Calculate ledger totals
    entries_qs = LedgerEntry.all_tenants.filter(
        foundation_id=foundation_id,
        school=school,
        occurred_at__gte=start_dt,
        occurred_at__lte=end_dt,
        deleted_at__isnull=True,
    )
    journals_qs = LedgerJournal.all_tenants.filter(
        foundation_id=foundation_id,
        school=school,
        occurred_at__gte=start_dt,
        occurred_at__lte=end_dt,
        deleted_at__isnull=True,
    )

    aggregates = entries_qs.aggregate(
        total_dr=Sum('debit'),
        total_cr=Sum('credit'),
    )
    total_debit = aggregates['total_dr'] or Decimal('0.00')
    total_credit = aggregates['total_cr'] or Decimal('0.00')
    total_journals = journals_qs.count()

    # Assert ledger balance
    if total_debit != total_credit:
        raise PeriodCloseValidationError(
            f"Buku besar periode {period} tidak seimbang: Total Debit ({total_debit}) != Total Kredit ({total_credit}). "
            "Periode tidak dapat ditutup."
        )

    now = timezone.now()
    if existing:
        fiscal_period = existing
        fiscal_period.status = FiscalPeriodStatus.CLOSED
        fiscal_period.closed_at = now
        fiscal_period.closed_by = closed_by
        fiscal_period.total_journals = total_journals
        fiscal_period.total_debit = total_debit
        fiscal_period.total_credit = total_credit
        fiscal_period.closing_notes = notes
        fiscal_period.save()
    else:
        fiscal_period = FiscalPeriod.all_tenants.create(
            foundation_id=foundation_id,
            school=school,
            period=period,
            status=FiscalPeriodStatus.CLOSED,
            closed_at=now,
            closed_by=closed_by,
            total_journals=total_journals,
            total_debit=total_debit,
            total_credit=total_credit,
            closing_notes=notes,
        )

    # Audit and domain event
    AuditEvent.objects.create(
        foundation_id=foundation_id,
        actor_id=str(closed_by.id) if closed_by else None,
        action="PERIOD_CLOSED",
        entity_type="FiscalPeriod",
        entity_id=str(fiscal_period.id),
        school_id=school.id,
        diff={
            "period": period,
            "status": FiscalPeriodStatus.CLOSED,
            "total_journals": total_journals,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "notes": notes,
        },
    )
    DomainEvent.objects.create(
        foundation_id=foundation_id,
        name="finance.period_closed",
        payload={
            "school_id": school.id,
            "period": period,
            "closed_at": now.isoformat(),
            "closed_by_id": closed_by.id if closed_by else None,
            "total_journals": total_journals,
        },
    )

    return fiscal_period


@transaction.atomic
def reopen_fiscal_period(
    school: School,
    period: str,
    reopened_by: Optional[User] = None,
    reason: str = '',
) -> FiscalPeriod:
    """
    Reopen a closed fiscal period for a school with required audit reason.
    """
    validate_period_format(period)
    if not reason or not reason.strip():
        raise ValueError(_("Alasan pembukaan kembali periode fiskal wajib diisi."))

    foundation_id = school.foundation_id
    fiscal_period = FiscalPeriod.all_tenants.filter(
        foundation_id=foundation_id,
        school=school,
        period=period,
        deleted_at__isnull=True,
    ).first()

    if not fiscal_period or fiscal_period.status != FiscalPeriodStatus.CLOSED:
        raise ValueError(_("Periode fiskal tidak dalam status tertutup."))

    now = timezone.now()
    prev_notes = fiscal_period.closing_notes or ''
    reopen_note = f"[Dibuka kembali oleh {reopened_by or 'Sistem'} pada {now.strftime('%Y-%m-%d %H:%M')}]: {reason}"
    fiscal_period.status = FiscalPeriodStatus.OPEN
    fiscal_period.reopened_at = now
    fiscal_period.reopened_by = reopened_by
    fiscal_period.closing_notes = f"{prev_notes}\n{reopen_note}".strip()
    fiscal_period.save()

    AuditEvent.objects.create(
        foundation_id=foundation_id,
        actor_id=str(reopened_by.id) if reopened_by else None,
        action="PERIOD_REOPENED",
        entity_type="FiscalPeriod",
        entity_id=str(fiscal_period.id),
        school_id=school.id,
        diff={
            "period": period,
            "status": FiscalPeriodStatus.OPEN,
            "reason": reason,
        },
    )
    DomainEvent.objects.create(
        foundation_id=foundation_id,
        name="finance.period_reopened",
        payload={
            "school_id": school.id,
            "period": period,
            "reopened_at": now.isoformat(),
            "reopened_by_id": reopened_by.id if reopened_by else None,
            "reason": reason,
        },
    )

    return fiscal_period

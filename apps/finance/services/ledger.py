from decimal import Decimal
from typing import Optional
from django.db import transaction
from django.utils import timezone

from apps.identity.models import User
from apps.finance.models import (
    AccountCode,
    Invoice,
    LedgerEntry,
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentSequence,
)


class UnbalancedLedgerError(ValueError):
    """Raised when journal debits do not equal credits (spec/06 §5 FIN-021, spec/16 CUR-020)."""
    pass


class CurrencyMismatchError(ValueError):
    """Raised when journal entries mix currencies (CUR-009)."""
    pass


def get_next_sequence_number(school, sequence_type: str, year: int = None) -> int:
    """Atomic gapless sequence generator per school, sequence type, and year."""
    if year is None:
        year = timezone.now().year

    seq, _ = PaymentSequence.objects.select_for_update().get_or_create(
        foundation_id=school.foundation_id,
        school=school,
        sequence_type=sequence_type,
        year=year,
        defaults={'last_number': 0},
    )
    seq.last_number += 1
    seq.save(update_fields=['last_number'])
    return seq.last_number


def get_school_code(school) -> str:
    """Derive clean school code from npsn or id (matching invoicing.py)."""
    if school is None:
        return "SCH00"
    if hasattr(school, 'npsn') and school.npsn:
        return school.npsn
    if hasattr(school, 'code') and school.code:
        return school.code
    return f"SCH{school.id}"


def get_next_journal_number(school, year: int = None) -> str:
    """Generate JRN/{school_code}/{YYYY}/{NNNNNN}."""
    if year is None:
        year = timezone.now().year
    num = get_next_sequence_number(school, 'JOURNAL', year)
    code = get_school_code(school)
    return f"JRN/{code}/{year}/{num:06d}"


def get_next_receipt_number(school, year: int = None) -> str:
    """Generate RCP/{school_code}/{YYYY}/{NNNNNN}."""
    if year is None:
        year = timezone.now().year
    num = get_next_sequence_number(school, 'RECEIPT', year)
    code = get_school_code(school)
    return f"RCP/{code}/{year}/{num:06d}"


def get_next_payment_reference(school, year: int = None) -> str:
    """Generate PAY/{school_code}/{YYYY}/{NNNNNN}."""
    if year is None:
        year = timezone.now().year
    num = get_next_sequence_number(school, 'PAYMENT', year)
    code = get_school_code(school)
    return f"PAY/{code}/{year}/{num:06d}"



@transaction.atomic
def post_ledger_journal(
    school,
    ref_type: str,
    ref_id: str,
    description: str,
    entries: list[dict],
    currency: str = 'IDR',
    occurred_at=None,
) -> LedgerJournal:
    """
    Persist a double-entry general ledger journal header and entries (FIN-021, CUR-020).
    Strictly asserts sum(debits) == sum(credits) to 0.00.
    """
    if occurred_at is None:
        occurred_at = timezone.now()

    if not entries:
        raise UnbalancedLedgerError("Cannot post an empty journal.")

    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')

    for entry in entries:
        entry_currency = entry.get('currency', currency)
        if entry_currency != currency:
            raise CurrencyMismatchError(
                f"Entry currency '{entry_currency}' does not match journal currency '{currency}'"
            )
        debit = Decimal(str(entry.get('debit', '0.00')))
        credit = Decimal(str(entry.get('credit', '0.00')))
        if debit < Decimal('0.00') or credit < Decimal('0.00'):
            raise UnbalancedLedgerError("Debit and credit amounts must be non-negative.")
        total_debit += debit
        total_credit += credit

    if total_debit != total_credit:
        raise UnbalancedLedgerError(
            f"Ledger journal unbalanced: total debit ({currency} {total_debit}) != total credit ({currency} {total_credit})"
        )

    journal_number = get_next_journal_number(school, occurred_at.year)
    journal = LedgerJournal.objects.create(
        foundation_id=school.foundation_id,
        school=school,
        number=journal_number,
        description=description,
        ref_type=ref_type,
        ref_id=str(ref_id),
        currency=currency,
        occurred_at=occurred_at,
    )

    entry_objects = []
    for entry in entries:
        entry_objects.append(
            LedgerEntry(
                foundation_id=school.foundation_id,
                school=school,
                journal=journal,
                account_code=entry['account_code'],
                account_name=entry.get('account_name', entry['account_code']),
                debit=Decimal(str(entry.get('debit', '0.00'))),
                credit=Decimal(str(entry.get('credit', '0.00'))),
                currency=currency,
                ref_type=ref_type,
                ref_id=str(ref_id),
                occurred_at=occurred_at,
            )
        )
    LedgerEntry.objects.bulk_create(entry_objects)
    return journal


@transaction.atomic
def post_invoice_issuance_journal(invoice: Invoice) -> LedgerJournal:
    """
    FIN-023: invoice issued → Dr AR / Cr Unearned (+/- Rounding).
    """
    entries = []
    # Dr AR (1200) Piutang SPP
    entries.append({
        'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
        'account_name': 'Piutang SPP & Biaya',
        'debit': invoice.total,
        'credit': Decimal('0.00'),
    })

    # Explicit rounding line if present
    if invoice.rounding < Decimal('0.00'):
        # Negative rounding: school gave a discount rounding down -> Dr Rounding Expense
        entries.append({
            'account_code': AccountCode.ROUNDING,
            'account_name': 'Beban Pembulatan',
            'debit': abs(invoice.rounding),
            'credit': Decimal('0.00'),
        })
    elif invoice.rounding > Decimal('0.00'):
        # Positive rounding: rounded up -> Cr Rounding Revenue
        entries.append({
            'account_code': AccountCode.ROUNDING,
            'account_name': 'Pendapatan Pembulatan',
            'debit': Decimal('0.00'),
            'credit': invoice.rounding,
        })

    # Cr Unearned Tuition (2100) Pendapatan Diterima di Muka
    unearned_amount = invoice.subtotal - invoice.discount
    entries.append({
        'account_code': AccountCode.UNEARNED_TUITION,
        'account_name': 'Pendapatan Diterima di Muka',
        'debit': Decimal('0.00'),
        'credit': unearned_amount,
    })

    return post_ledger_journal(
        school=invoice.school,
        ref_type='INVOICE',
        ref_id=invoice.id,
        description=f"Penerbitan Tagihan {invoice.number} ({invoice.student.person.full_name})",
        entries=entries,
        currency=invoice.currency,
        occurred_at=timezone.now(),
    )


@transaction.atomic
def post_payment_settlement_journal(
    payment: Payment,
    allocations: list[PaymentAllocation],
    overpayment: Decimal = Decimal('0.00'),
) -> LedgerJournal:
    """
    FIN-023: payment settled → Dr Cash, Dr Fee Expense / Cr AR, Cr Student Credit (if overpaid).
    """
    entries = []
    # Dr Cash/Bank (1100)
    entries.append({
        'account_code': AccountCode.CASH_BANK,
        'account_name': 'Kas / Bank',
        'debit': payment.net,
        'credit': Decimal('0.00'),
    })

    # Dr Fee Expense (5100) if gateway fee exists
    if payment.fee > Decimal('0.00'):
        entries.append({
            'account_code': AccountCode.PAYMENT_FEES,
            'account_name': 'Beban Transaksi & Gateway',
            'debit': payment.fee,
            'credit': Decimal('0.00'),
        })

    # Cr AR (1200) for allocated amounts
    allocated_total = sum(a.amount for a in allocations)
    if allocated_total > Decimal('0.00'):
        entries.append({
            'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
            'account_name': 'Piutang SPP & Biaya',
            'debit': Decimal('0.00'),
            'credit': allocated_total,
        })

    # Cr Student Credit Balance (2200) if overpaid (FIN-015)
    if overpayment > Decimal('0.00'):
        entries.append({
            'account_code': AccountCode.STUDENT_CREDIT,
            'account_name': 'Saldo Deposit Siswa',
            'debit': Decimal('0.00'),
            'credit': overpayment,
        })

    return post_ledger_journal(
        school=payment.school,
        ref_type='PAYMENT',
        ref_id=payment.id,
        description=f"Penerimaan Pembayaran {payment.reference} ({payment.channel})",
        entries=entries,
        currency=payment.currency,
        occurred_at=payment.settled_at or timezone.now(),
    )


@transaction.atomic
def post_revenue_recognition_journal(
    invoice: Invoice,
    recognized_amount: Decimal = None,
) -> LedgerJournal:
    """
    FIN-023: period recognised → Dr Unearned / Cr Revenue.
    """
    amount = recognized_amount if recognized_amount is not None else (invoice.subtotal - invoice.discount)
    entries = [
        {
            'account_code': AccountCode.UNEARNED_TUITION,
            'account_name': 'Pendapatan Diterima di Muka',
            'debit': amount,
            'credit': Decimal('0.00'),
        },
        {
            'account_code': AccountCode.TUITION_REVENUE,
            'account_name': 'Pendapatan SPP',
            'debit': Decimal('0.00'),
            'credit': amount,
        },
    ]
    return post_ledger_journal(
        school=invoice.school,
        ref_type='REVENUE_RECOGNITION',
        ref_id=invoice.id,
        description=f"Pengakuan Pendapatan SPP Periode {invoice.period} Tagihan {invoice.number}",
        entries=entries,
        currency=invoice.currency,
        occurred_at=timezone.now(),
    )


def post_write_off_journal(
    invoice: Invoice,
    amount: Decimal,
    user: Optional[User] = None,
    reason: str = "",
) -> LedgerJournal:
    """
    FIN-031: Bad debt write-off journal → Dr Bad Debt / Cr AR.
    """
    entries = [
        {
            'account_code': AccountCode.BAD_DEBT_EXPENSE,
            'account_name': 'Beban Piutang Tak Tertagih',
            'debit': amount,
            'credit': Decimal('0.00'),
        },
        {
            'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
            'account_name': 'Piutang SPP & Biaya',
            'debit': Decimal('0.00'),
            'credit': amount,
        },
    ]
    description = f"Penghapusbukuan Piutang Tak Tertagih Tagihan {invoice.number}"
    if reason:
        description += f": {reason}"

    return post_ledger_journal(
        school=invoice.school,
        ref_type='WRITE_OFF',
        ref_id=invoice.id,
        description=description,
        entries=entries,
        currency=invoice.currency,
        occurred_at=timezone.now(),
    )

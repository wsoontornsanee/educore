"""Oversight of student-entered QR charges: disputes, merchant flag, underpayment signals (spec 18 §7).

Nothing here reverses a sale automatically. A guardian opens a dispute
(QRS-026); school staff resolve it, and an upheld outcome is its own
REFUND/ADJUSTMENT ledger line. Underpayment is surfaced as a report for a
conversation, not enforced (QRS-027).

Lock order for anything that touches a disputed sale: the student's wallet, then the sale, then the
sale's dispute. Charge and void already take wallet then sale; dispute paths add the dispute last, so a
void that closes an open dispute and a staff resolution of it queue behind each other instead of deadlocking.
"""
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.wallet.models import (
    POSEntryMode,
    POSTransaction,
    MerchantSettlementAdjustment,
    POSTransactionStatus,
    QRDispute,
    QRDisputeStatus,
    WalletTransactionType,
)
from apps.wallet.services import _get_locked_wallet, record_wallet_transaction

DISPUTE_WINDOW_DAYS = 7  # QRS-026
DISPUTE_FLAG_THRESHOLD = 3  # QRS-028: upheld disputes...
DISPUTE_FLAG_WINDOW_DAYS = 30  # ...within this many days flag the merchant
UNDERPAYMENT_BASELINE_DAYS = 30
UNDERPAYMENT_MIN_BASKETS = 10  # below this a p10 is noise, so no signals are produced


class QRDisputeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


@transaction.atomic
def open_qr_dispute(pos_transaction: POSTransaction, opened_by, reason: str) -> QRDispute:
    """QRS-026: a guardian contests a self-entered charge within 7 days. One dispute per charge.

    Runs under the wallet and sale locks (see the module docstring), so two concurrent opens for one sale
    are serialised and the second sees the first (DISPUTE_EXISTS), and an open cannot slip in between a
    void's status check and its close of open disputes: it either sees the sale still COMPLETED before the
    void or already VOIDED after it."""
    if pos_transaction.entry_mode != POSEntryMode.SELF_ENTERED:
        raise QRDisputeError('DISPUTE_NOT_ELIGIBLE', _("Hanya pembayaran QR yang diinput siswa dan masih berlaku yang dapat disanggah."))
    _get_locked_wallet(pos_transaction.wallet_transaction.wallet_id, pos_transaction.foundation_id)
    pos_transaction = POSTransaction.all_tenants.select_for_update().get(
        id=pos_transaction.id, foundation_id=pos_transaction.foundation_id,
    )
    if pos_transaction.status != POSTransactionStatus.COMPLETED:
        raise QRDisputeError('DISPUTE_NOT_ELIGIBLE', _("Hanya pembayaran QR yang diinput siswa dan masih berlaku yang dapat disanggah."))
    if timezone.now() - pos_transaction.occurred_at > timedelta(days=DISPUTE_WINDOW_DAYS):
        raise QRDisputeError('DISPUTE_WINDOW_CLOSED', _("Batas waktu sanggahan 7 hari sudah lewat."))
    if QRDispute.objects.filter(
        foundation_id=pos_transaction.foundation_id, pos_transaction=pos_transaction, deleted_at__isnull=True,
    ).exists():
        raise QRDisputeError('DISPUTE_EXISTS', _("Pembayaran ini sudah pernah disanggah."))

    dispute = QRDispute.objects.create(
        foundation_id=pos_transaction.foundation_id, pos_transaction=pos_transaction,
        merchant=pos_transaction.merchant, student=pos_transaction.student, opened_by=opened_by, reason=reason,
    )
    audit(
        action='wallet.qr_dispute.opened', entity_type='QRDispute', entity_id=dispute.id,
        foundation_id=dispute.foundation_id, diff={'pos_transaction': pos_transaction.id, 'total': str(pos_transaction.total)},
    )
    return dispute


@transaction.atomic
def resolve_qr_dispute(
    dispute: QRDispute, outcome: str, actor, note: str = '', refund_amount: Optional[Decimal] = None,
) -> QRDispute:
    """Close an open dispute. UPHELD credits the wallet (a full refund is a REFUND, a smaller amount an
    ADJUSTMENT) and records a settlement adjustment so the merchant gives the money back at its next unpaid
    settlement. The sale itself stays COMPLETED either way."""
    if outcome not in (QRDisputeStatus.UPHELD, QRDisputeStatus.REJECTED):
        raise QRDisputeError('DISPUTE_INVALID_OUTCOME', _("Hasil sanggahan tidak valid."))
    # Wallet, then sale, then the dispute (see the module docstring): the order void and charge use, so a
    # void cannot slip in between the status check and the refund, and a void closing this very dispute
    # queues behind us rather than deadlocking. The unlocked read only finds the wallet to lock first.
    dispute = QRDispute.objects.select_related('pos_transaction__wallet_transaction').get(
        id=dispute.id, foundation_id=dispute.foundation_id,
    )
    _get_locked_wallet(dispute.pos_transaction.wallet_transaction.wallet_id, dispute.foundation_id)
    pos_tx = POSTransaction.all_tenants.select_for_update().get(
        id=dispute.pos_transaction_id, foundation_id=dispute.foundation_id,
    )
    dispute = QRDispute.objects.select_for_update().get(id=dispute.id, foundation_id=dispute.foundation_id)
    if dispute.status != QRDisputeStatus.OPEN:
        raise QRDisputeError('DISPUTE_NOT_OPEN', _("Sanggahan ini sudah diselesaikan."))
    if outcome == QRDisputeStatus.UPHELD:
        if pos_tx.status != POSTransactionStatus.COMPLETED:
            # A void already refunded this sale in full; upholding would credit the guardian a second time.
            # Voiding now closes the sale's open dispute itself, so this only meets cases left OPEN before that.
            raise QRDisputeError('DISPUTE_SALE_VOIDED', _("Pembayaran ini sudah dibatalkan dan dikembalikan penuh, sehingga sanggahan tidak dapat dikabulkan."))
        amount = pos_tx.total if refund_amount is None else Decimal(str(refund_amount)).quantize(Decimal('0.01'))
        if amount <= Decimal('0.00') or amount > pos_tx.total:
            raise QRDisputeError('DISPUTE_INVALID_AMOUNT', _("Jumlah pengembalian harus lebih dari nol dan tidak melebihi pembayaran."))
        dispute.resolution_transaction = record_wallet_transaction(
            pos_tx.wallet_transaction.wallet,
            WalletTransactionType.REFUND if amount == pos_tx.total else WalletTransactionType.ADJUSTMENT,
            amount, f"dispute:{dispute.id}", reference=f"DISPUTE:{dispute.merchant.name}",
        )
        dispute.refund_amount = amount

    dispute.status = outcome
    dispute.resolved_by = actor
    dispute.resolved_at = timezone.now()
    dispute.resolution_note = note
    dispute.save()
    if outcome == QRDisputeStatus.UPHELD:
        # The sale stays COMPLETED (it happened and was refunded); the merchant's side is netted at settlement.
        _record_settlement_adjustment(dispute, pos_tx)
    audit(
        action='wallet.qr_dispute.resolved', entity_type='QRDispute', entity_id=dispute.id,
        foundation_id=dispute.foundation_id, diff={'outcome': outcome, 'refund_amount': str(dispute.refund_amount)},
    )
    if outcome == QRDisputeStatus.UPHELD:
        _flag_merchant_if_needed(dispute.merchant)
    return dispute


def close_disputes_for_voided_sale(pos_tx: POSTransaction, refund_transaction, actor=None) -> int:
    """Close the sale's OPEN dispute(s) as VOIDED: the void just refunded the guardian in full, so there is
    nothing left for staff to decide. Not UPHELD: no second refund, no settlement adjustment (the void already
    reverses the merchant's side) and no count toward the merchant flag (QRS-028).

    Called by `void_pos_transaction` with the wallet and the sale already locked; takes the dispute last.
    Returns how many disputes were closed.
    """
    disputes = list(QRDispute.objects.select_for_update().filter(
        foundation_id=pos_tx.foundation_id, pos_transaction=pos_tx, status=QRDisputeStatus.OPEN,
        deleted_at__isnull=True,
    ))
    now = timezone.now()
    for dispute in disputes:
        dispute.status = QRDisputeStatus.VOIDED
        dispute.resolved_by = actor if getattr(actor, 'pk', None) else None
        dispute.resolved_at = now
        dispute.resolution_note = _("Pembayaran dibatalkan; dana dikembalikan penuh.")
        dispute.resolution_transaction = refund_transaction
        dispute.save()
        audit(
            action='wallet.qr_dispute.closed_by_void', entity_type='QRDispute', entity_id=dispute.id,
            foundation_id=dispute.foundation_id, diff={'pos_transaction': pos_tx.id},
        )
    return len(disputes)


def _record_settlement_adjustment(dispute: QRDispute, pos_tx: POSTransaction) -> MerchantSettlementAdjustment:
    """deduction = refund - the commission share on it, i.e. what voiding that portion would have removed."""
    refund = dispute.refund_amount
    recovered = (pos_tx.commission * refund / pos_tx.total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if pos_tx.total else Decimal('0.00')
    adjustment = MerchantSettlementAdjustment.objects.create(
        foundation_id=dispute.foundation_id, merchant=dispute.merchant, dispute=dispute, pos_transaction=pos_tx,
        refund_amount=refund, commission_recovered=recovered, deduction=refund - recovered,
        occurred_at=dispute.resolved_at,
    )
    audit(
        action='wallet.settlement_adjustment.created', entity_type='MerchantSettlementAdjustment',
        entity_id=adjustment.id, foundation_id=dispute.foundation_id,
        diff={'deduction': str(adjustment.deduction), 'refund': str(refund), 'commission_recovered': str(recovered)},
    )
    return adjustment


def _flag_merchant_if_needed(merchant) -> None:
    """QRS-028: 3+ upheld disputes in 30 days raise a flag for school-admin review."""
    since = timezone.now() - timedelta(days=DISPUTE_FLAG_WINDOW_DAYS)
    upheld = QRDispute.objects.filter(
        foundation_id=merchant.foundation_id, merchant=merchant, status=QRDisputeStatus.UPHELD,
        resolved_at__gte=since, deleted_at__isnull=True,
    ).count()
    if upheld >= DISPUTE_FLAG_THRESHOLD and merchant.qr_dispute_flagged_at is None:
        merchant.qr_dispute_flagged_at = timezone.now()
        merchant.save(update_fields=['qr_dispute_flagged_at', 'updated_at'])
        audit(
            action='wallet.merchant.qr_dispute_flagged', entity_type='Merchant', entity_id=merchant.id,
            foundation_id=merchant.foundation_id, diff={'upheld_in_window': upheld},
        )


def clear_merchant_qr_flag(merchant, actor) -> None:
    """School-admin review closes the QRS-028 flag."""
    if merchant.qr_dispute_flagged_at is None:
        return
    merchant.qr_dispute_flagged_at = None
    merchant.save(update_fields=['qr_dispute_flagged_at', 'updated_at'])
    audit(
        action='wallet.merchant.qr_dispute_flag_cleared', entity_type='Merchant', entity_id=merchant.id,
        foundation_id=merchant.foundation_id, diff={'by': getattr(actor, 'id', None)},
    )


def get_underpayment_signals(merchant, day: Optional[date] = None) -> Dict[str, Any]:
    """QRS-027: self-entered charges below p10 of the merchant's itemised baskets, grouped by student.

    The baseline is the merchant's operator-entered sales over the prior 30 days (nearest-rank p10,
    computed here because MySQL has no percentile). With too few baskets the report says so instead
    of guessing.
    """
    from apps.attendance.services import get_school_timezone

    school_tz = get_school_timezone(merchant.school)
    day = day or timezone.now().astimezone(school_tz).date()
    day_start = datetime.combine(day, time.min, tzinfo=school_tz)
    day_end = day_start + timedelta(days=1)

    base = POSTransaction.objects.filter(
        foundation_id=merchant.foundation_id, merchant=merchant, deleted_at__isnull=True,
        status=POSTransactionStatus.COMPLETED,
    )
    baskets = sorted(base.filter(
        entry_mode=POSEntryMode.OPERATOR,
        occurred_at__gte=day_start - timedelta(days=UNDERPAYMENT_BASELINE_DAYS), occurred_at__lt=day_start,
    ).values_list('total', flat=True))

    report: Dict[str, Any] = {'date': day.isoformat(), 'baseline_baskets': len(baskets), 'p10': None, 'students': []}
    if len(baskets) < UNDERPAYMENT_MIN_BASKETS:
        report['insufficient_baseline'] = True
        return report
    p10 = baskets[math.ceil(0.10 * len(baskets)) - 1]
    report['p10'] = p10
    report['insufficient_baseline'] = False

    grouped = defaultdict(list)
    for tx in base.filter(
        entry_mode=POSEntryMode.SELF_ENTERED, occurred_at__gte=day_start, occurred_at__lt=day_end, total__lt=p10,
    ).select_related('student', 'student__person').order_by('occurred_at'):
        grouped[tx.student_id].append(tx)
    for txs in grouped.values():
        student = txs[0].student
        report['students'].append({
            'student_id': student.id, 'student_name': student.person.full_name, 'student_nis': student.nis,
            'count': len(txs), 'total': sum((t.total for t in txs), Decimal('0.00')),
            'transactions': [
                {'id': t.id, 'amount': t.total, 'confirmation_code': t.confirmation_code, 'occurred_at': t.occurred_at}
                for t in txs
            ],
        })
    report['students'].sort(key=lambda s: (-s['count'], s['student_name']))
    return report

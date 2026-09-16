import logging
from decimal import Decimal
from typing import Any, Optional
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.services import audit, record_domain_event
from apps.identity.models import Foundation, RoleAssignment, User
from apps.identity.rbac import is_foundation_admin
from apps.finance.models import (
    AccountCode,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    StudentCreditBalance,
)

logger = logging.getLogger(__name__)

DEFAULT_REFUND_APPROVAL_THRESHOLD = Decimal('1000000.00')


class RefundValidationError(ValueError):
    """Raised when refund request validation fails."""
    pass


class ExceededPaymentAmountError(ValueError):
    """Raised when cumulative refunds exceed settled payment amount (FIN-034)."""
    pass


class InvalidRefundStateError(ValueError):
    """Raised when an operation is invalid for the current refund state."""
    pass


@transaction.atomic
def request_refund(
    payment: Payment,
    amount: Decimal,
    reason: str,
    destination_bank_name: str,
    destination_account_number: str,
    destination_account_holder: str,
    requested_by: User,
    currency: str = 'IDR',
    approval_threshold: Optional[Decimal] = None,
) -> Refund:
    """
    Submits a refund request against a settled payment (spec/06 §7, FIN-020, FIN-032, FIN-034).
    Enforces cumulative refund limit <= payment.amount.
    Gated by the payment's foundation.approval_threshold (FND-007, FIN-032).
    """
    if approval_threshold is None:
        approval_threshold = Foundation.objects.filter(id=payment.foundation_id).values_list(
            'approval_threshold', flat=True
        ).first() or DEFAULT_REFUND_APPROVAL_THRESHOLD

    # Lock payment record for inspection
    payment = Payment.objects.select_for_update().get(id=payment.id)

    if payment.status != PaymentStatus.SETTLED:
        raise RefundValidationError(_("Hanya pembayaran dengan status SETTLED yang dapat diajukan pengembalian dana."))

    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    if amount <= Decimal('0.00'):
        raise RefundValidationError(_("Nominal pengembalian dana harus lebih dari 0.00."))

    if currency != payment.currency:
        raise RefundValidationError(
            _(f"Mata uang pengembalian ({currency}) tidak sesuai dengan mata uang pembayaran ({payment.currency}).")
        )

    if not destination_bank_name or not destination_bank_name.strip():
        raise RefundValidationError(_("Nama bank tujuan wajib diisi."))

    if not destination_account_number or not destination_account_number.strip():
        raise RefundValidationError(_("Nomor rekening tujuan wajib diisi."))

    if not destination_account_holder or not destination_account_holder.strip():
        raise RefundValidationError(_("Nama pemilik rekening tujuan wajib diisi."))

    if not reason or not reason.strip():
        raise RefundValidationError(_("Alasan pengembalian dana wajib diisi."))

    # Check cumulative refunds against settled payment amount (FIN-034)
    existing_refunds = Refund.objects.filter(
        foundation_id=payment.foundation_id,
        payment=payment,
    ).exclude(status__in=[RefundStatus.REJECTED, RefundStatus.CANCELLED])

    cumulative_amount = sum(r.amount for r in existing_refunds)
    if cumulative_amount + amount > payment.amount:
        raise ExceededPaymentAmountError(
            _(f"Total pengembalian dana ({cumulative_amount + amount}) melebihi nominal pembayaran yang diselesaikan ({payment.amount}).")
        )

    # Check threshold policy (FIN-032, FND-007)
    is_admin = is_foundation_admin(requested_by, payment.foundation_id)

    if amount > approval_threshold and not is_admin:
        status = RefundStatus.PENDING_APPROVAL
        approved_by = None
        approved_at = None
    else:
        status = RefundStatus.APPROVED
        approved_by = requested_by
        approved_at = timezone.now()

    refund = Refund.objects.create(
        foundation_id=payment.foundation_id,
        school=payment.school,
        student=payment.student,
        payment=payment,
        amount=amount,
        currency=currency,
        reason=reason.strip(),
        destination_bank_name=destination_bank_name.strip(),
        destination_account_number=destination_account_number.strip(),
        destination_account_holder=destination_account_holder.strip(),
        status=status,
        requested_by=requested_by,
        approved_by=approved_by,
        approved_at=approved_at,
    )

    audit(
        action='finance.refund.requested',
        entity_type='Refund',
        entity_id=refund.id,
        actor_id=str(requested_by.id),
        role='foundation_admin' if is_admin else 'finance_officer',
        foundation_id=refund.foundation_id,
        school_id=refund.school_id,
        diff={
            'payment_id': payment.id,
            'payment_reference': payment.reference,
            'amount': str(amount),
            'currency': currency,
            'status': status,
            'destination_bank': destination_bank_name.strip(),
            'destination_account': destination_account_number.strip(),
        },
    )

    record_domain_event(
        name='finance.refund_requested',
        payload={
            'refund_id': refund.id,
            'payment_id': payment.id,
            'amount': str(amount),
            'currency': currency,
            'status': status,
        },
        foundation_id=refund.foundation_id,
    )

    return refund


@transaction.atomic
def approve_refund(
    refund: Refund,
    user: User,
    decision: str,
    reason: str = '',
) -> Refund:
    """
    Approves or rejects a pending refund request (spec/06 §7, FIN-032, FND-007, FND-008).
    """
    if not is_foundation_admin(user, refund.foundation_id):
        raise PermissionDenied(
            _("Persetujuan atau penolakan pengembalian dana memerlukan wewenang Admin Yayasan (FND-007, FIN-032).")
        )

    if refund.status != RefundStatus.PENDING_APPROVAL:
        raise InvalidRefundStateError(
            _("Hanya pengembalian dana dengan status PENDING_APPROVAL yang dapat diproses persetujuannya.")
        )

    decision_upper = decision.upper().strip()
    if decision_upper not in ['APPROVE', 'REJECT']:
        raise RefundValidationError(_("Keputusan harus 'APPROVE' atau 'REJECT'."))

    if decision_upper == 'REJECT' and not reason.strip():
        raise RefundValidationError(_("Alasan penolakan wajib diisi jika permohonan ditolak."))

    now = timezone.now()
    if decision_upper == 'APPROVE':
        refund.status = RefundStatus.APPROVED
        refund.approved_by = user
        refund.approved_at = now
        refund.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

        audit(
            action='finance.refund.approved',
            entity_type='Refund',
            entity_id=refund.id,
            actor_id=str(user.id),
            role='foundation_admin',
            foundation_id=refund.foundation_id,
            school_id=refund.school_id,
            diff={'status': RefundStatus.APPROVED},
        )
        record_domain_event(
            name='finance.refund_approved',
            payload={
                'refund_id': refund.id,
                'payment_id': refund.payment_id,
                'amount': str(refund.amount),
                'status': refund.status,
            },
            foundation_id=refund.foundation_id,
        )
    else:
        refund.status = RefundStatus.REJECTED
        refund.rejection_reason = reason.strip()
        refund.save(update_fields=['status', 'rejection_reason', 'updated_at'])

        audit(
            action='finance.refund.rejected',
            entity_type='Refund',
            entity_id=refund.id,
            actor_id=str(user.id),
            role='foundation_admin',
            foundation_id=refund.foundation_id,
            school_id=refund.school_id,
            diff={'status': RefundStatus.REJECTED, 'reason': reason.strip()},
        )
        record_domain_event(
            name='finance.refund_rejected',
            payload={
                'refund_id': refund.id,
                'payment_id': refund.payment_id,
                'amount': str(refund.amount),
                'status': refund.status,
                'reason': reason.strip(),
            },
            foundation_id=refund.foundation_id,
        )

    return refund


@transaction.atomic
def execute_refund(
    refund: Refund,
    user: User,
    payout_reference: str,
    payout_proof_file: str = '',
) -> Refund:
    """
    Executes an approved refund (spec/06 §7, FIN-020, FIN-021, FIN-033).
    - Records payout reference & proof.
    - Reverses overpayments (StudentCreditBalance) and/or invoice allocations (Invoice.paid).
    - Posts compensating balanced double-entry ledger journal:
      Dr Accounts Receivable (1200) and/or Dr Student Credit (2200) / Cr Cash/Bank (1100).
    - Dispatches guardian notification.
    """
    if refund.status != RefundStatus.APPROVED:
        raise InvalidRefundStateError(
            _("Hanya pengembalian dana dengan status APPROVED yang dapat dieksekusi.")
        )

    if not payout_reference or not payout_reference.strip():
        raise RefundValidationError(_("Nomor referensi payout atau bukti transfer bank wajib diisi."))

    payment = Payment.objects.select_for_update().get(id=refund.payment_id)

    # Previously executed refunds on this payment
    previously_executed = Refund.objects.filter(
        payment=payment,
        status=RefundStatus.EXECUTED,
    ).exclude(id=refund.id)
    prev_executed_total = sum(r.amount for r in previously_executed)

    if prev_executed_total + refund.amount > payment.amount:
        raise ExceededPaymentAmountError(_("Total pengembalian dana melebihi nominal pembayaran."))

    # Payment allocations & overpayment resolution
    allocations = list(payment.allocations.select_related('invoice').order_by('id'))
    total_allocated = sum(a.amount for a in allocations)
    initial_overpayment = max(Decimal('0.00'), payment.amount - total_allocated)

    # Overpayment exhaustion order: overpayment is reversed first
    prior_overpayment_reversed = min(initial_overpayment, prev_executed_total)
    avail_overpayment = max(Decimal('0.00'), initial_overpayment - prior_overpayment_reversed)

    refund_from_overpayment = min(refund.amount, avail_overpayment)
    refund_from_invoices = refund.amount - refund_from_overpayment

    entries = []

    # 1. Reverse overpayment if applicable
    if refund_from_overpayment > Decimal('0.00'):
        credit = StudentCreditBalance.objects.select_for_update().filter(
            foundation_id=payment.foundation_id,
            student=payment.student,
            currency=payment.currency,
        ).first()
        if credit:
            credit.balance = max(Decimal('0.00'), credit.balance - refund_from_overpayment)
            credit.save(update_fields=['balance', 'updated_at'])

        entries.append({
            'account_code': AccountCode.STUDENT_CREDIT,
            'account_name': 'Saldo Deposit Siswa',
            'debit': refund_from_overpayment,
            'credit': Decimal('0.00'),
        })

    # 2. Reverse invoice allocations if applicable
    if refund_from_invoices > Decimal('0.00'):
        prior_invoice_reversed = max(Decimal('0.00'), prev_executed_total - initial_overpayment)
        rem_prior = prior_invoice_reversed
        rem_to_reverse = refund_from_invoices

        for alloc in reversed(allocations):
            alloc_amt = alloc.amount
            if rem_prior >= alloc_amt:
                rem_prior -= alloc_amt
                continue
            elif rem_prior > Decimal('0.00'):
                avail_alloc = alloc_amt - rem_prior
                rem_prior = Decimal('0.00')
            else:
                avail_alloc = alloc_amt

            cur_reverse = min(rem_to_reverse, avail_alloc)
            if cur_reverse > Decimal('0.00'):
                inv = alloc.invoice
                inv.paid = max(Decimal('0.00'), inv.paid - cur_reverse)
                if inv.paid <= Decimal('0.00'):
                    inv.status = InvoiceStatus.ISSUED
                elif inv.paid < inv.total:
                    inv.status = InvoiceStatus.PARTIALLY_PAID
                inv.save(update_fields=['paid', 'status', 'updated_at'])

                rem_to_reverse -= cur_reverse
                if rem_to_reverse <= Decimal('0.00'):
                    break

        entries.append({
            'account_code': AccountCode.ACCOUNTS_RECEIVABLE,
            'account_name': 'Piutang SPP & Biaya',
            'debit': refund_from_invoices,
            'credit': Decimal('0.00'),
        })

    # 3. Credit Cash / Bank (1100)
    entries.append({
        'account_code': AccountCode.CASH_BANK,
        'account_name': 'Kas / Bank',
        'debit': Decimal('0.00'),
        'credit': refund.amount,
    })

    # Post double-entry ledger journal
    from apps.finance.services.ledger import post_ledger_journal
    journal = post_ledger_journal(
        school=refund.school,
        ref_type='REFUND',
        ref_id=refund.id,
        description=f"Pengembalian Dana Pembayaran {payment.reference} ({refund.reason[:100]})",
        entries=entries,
        currency=refund.currency,
        occurred_at=timezone.now(),
        allow_next_period_routing=True,
    )

    now = timezone.now()
    refund.status = RefundStatus.EXECUTED
    refund.executed_by = user
    refund.executed_at = now
    refund.payout_reference = payout_reference.strip()
    refund.payout_proof_file = payout_proof_file.strip()
    refund.journal = journal
    refund.save(update_fields=[
        'status', 'executed_by', 'executed_at', 'payout_reference',
        'payout_proof_file', 'journal', 'updated_at',
    ])

    # Dispatch guardian notification (FIN-033)
    try:
        from apps.identity.models import GuardianLink
        from apps.notifications.services import dispatch_intent
        from apps.notifications.models import NotificationCategory, NotificationPriority

        guardian_link = (
            GuardianLink.objects.filter(
                student=refund.student,
                deleted_at__isnull=True,
            )
            .filter(models.Q(is_primary=True) | models.Q(financial_responsible=True))
            .select_related('guardian__user', 'guardian__person')
            .first()
        )
        if not guardian_link:
            guardian_link = (
                GuardianLink.objects.filter(
                    student=refund.student,
                    deleted_at__isnull=True,
                )
                .select_related('guardian__user', 'guardian__person')
                .first()
            )

        if guardian_link and guardian_link.guardian:
            guardian_user = guardian_link.guardian.user
            guardian_person = guardian_link.guardian.person
            phone = guardian_user.phone_e164 if guardian_user and guardian_user.phone_e164 else ''
            name = guardian_person.full_name if guardian_person else ''

            dispatch_intent(
                foundation_id=refund.foundation_id,
                category=NotificationCategory.PAYMENT_RECEIVED,
                template_key='finance.refund_executed',
                school_id=refund.school_id,
                recipient_user=guardian_user,
                recipient_phone=phone,
                recipient_name=name,
                payload={
                    'student_name': refund.student.person.full_name if refund.student and refund.student.person else '',
                    'payment_reference': payment.reference,
                    'amount': str(refund.amount),
                    'currency': refund.currency,
                    'payout_reference': refund.payout_reference,
                    'bank_name': refund.destination_bank_name,
                    'account_number': refund.destination_account_number,
                    'account_holder': refund.destination_account_holder,
                },
                priority=NotificationPriority.HIGH,
                dedupe_key=f"refund_exec_{refund.id}",
                immediate=False,
            )
    except Exception as e:
        logger.warning(f"Failed to dispatch guardian notification for refund {refund.id}: {e}")

    audit(
        action='finance.refund.executed',
        entity_type='Refund',
        entity_id=refund.id,
        actor_id=str(user.id),
        role='finance_officer',
        foundation_id=refund.foundation_id,
        school_id=refund.school_id,
        diff={
            'status': RefundStatus.EXECUTED,
            'payout_reference': refund.payout_reference,
            'amount': str(refund.amount),
            'journal_id': journal.id,
        },
    )

    record_domain_event(
        name='finance.refund_executed',
        payload={
            'refund_id': refund.id,
            'payment_id': refund.payment_id,
            'amount': str(refund.amount),
            'payout_reference': refund.payout_reference,
            'journal_number': journal.number,
        },
        foundation_id=refund.foundation_id,
    )

    return refund


@transaction.atomic
def cancel_refund(
    refund: Refund,
    user: User,
    reason: str = '',
) -> Refund:
    """
    Cancels an unexecuted refund request.
    """
    if refund.status not in [RefundStatus.PENDING_APPROVAL, RefundStatus.APPROVED]:
        raise InvalidRefundStateError(
            _("Hanya pengembalian dana yang belum dieksekusi yang dapat dibatalkan.")
        )

    refund.status = RefundStatus.CANCELLED
    refund.rejection_reason = reason.strip()
    refund.save(update_fields=['status', 'rejection_reason', 'updated_at'])

    audit(
        action='finance.refund.cancelled',
        entity_type='Refund',
        entity_id=refund.id,
        actor_id=str(user.id),
        role='finance_officer',
        foundation_id=refund.foundation_id,
        school_id=refund.school_id,
        diff={'status': RefundStatus.CANCELLED, 'reason': reason.strip()},
    )

    record_domain_event(
        name='finance.refund_cancelled',
        payload={
            'refund_id': refund.id,
            'payment_id': refund.payment_id,
            'amount': str(refund.amount),
            'status': refund.status,
            'reason': reason.strip(),
        },
        foundation_id=refund.foundation_id,
    )

    return refund

from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.core.services import audit, record_domain_event

from apps.finance.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentAllocation,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentStatus,
    StudentCreditBalance,
    StudentVirtualAccount,
)
from apps.finance.services.ledger import (
    get_next_payment_reference,
    get_next_receipt_number,
    post_payment_settlement_journal,
)
from apps.finance.services.convenience_fee import calculate_convenience_fee
from apps.finance.services.payment_providers import get_payment_provider
from apps.identity.models import School, Student


logger = __import__('logging').getLogger(__name__)


class CurrencyMismatchError(ValueError):
    pass


class InvalidPaymentError(ValueError):
    pass


def get_or_create_student_va(
    student: Student,
    school: School,
    bank: str,
    provider_name: str = 'MOCK',
) -> StudentVirtualAccount:
    """
    FIN-011: Stable per-student Virtual Account mapping per bank.
    """
    bank_clean = bank.upper().strip()
    va = StudentVirtualAccount.objects.filter(
        foundation_id=school.foundation_id,
        student=student,
        bank=bank_clean,
        is_active=True,
    ).first()
    if va:
        return va

    provider = get_payment_provider(provider_name)
    now = timezone.now()
    va_data = provider.create_va(student=student, school=school, bank=bank_clean, amount=Decimal('0.00'), expires_at=now + timedelta(days=365))
    
    va = StudentVirtualAccount.objects.create(
        foundation_id=school.foundation_id,
        student=student,
        bank=bank_clean,
        va_number=va_data['va_number'],
        is_active=True,
    )
    return va


@transaction.atomic
def create_payment_intent(
    school: School,
    student: Student,
    invoice_ids: list[int],
    method: str,
    bank: str = None,
    provider_name: str = 'MOCK',
    amount: Decimal = None,
) -> PaymentIntent:
    """
    Create a PaymentIntent for one or multiple invoices (spec/06 §2, §4).
    Enforces same-currency validation (CUR-009, FIN-013b).
    """
    invoices = list(
        Invoice.objects.filter(
            foundation_id=school.foundation_id,
            school=school,
            student=student,
            id__in=invoice_ids,
        ).exclude(status__in=[InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF])
    )
    if not invoices:
        raise InvalidPaymentError("No unpaid invoices found for payment intent.")

    # Validate single currency
    currencies = {inv.currency for inv in invoices}
    if len(currencies) > 1:
        raise CurrencyMismatchError(f"Cannot mix currencies in a single payment intent: {currencies}")
    currency = list(currencies)[0]

    # Calculate total balance due
    total_due = sum(inv.balance_due for inv in invoices)
    base_amount = amount if amount is not None else total_due
    if base_amount <= Decimal('0.00'):
        raise InvalidPaymentError("Payment intent amount must be positive.")

    # FIN-017: convenience fee applies only to gateway-mediated methods (VA, QRIS) —
    # manual transfer and cash never go through a payment gateway, so there's no fee.
    convenience_fee_amount = Decimal('0.00')
    if method in (PaymentMethod.VA, PaymentMethod.QRIS):
        convenience_fee_amount = calculate_convenience_fee(school, base_amount)
    intent_amount = base_amount + convenience_fee_amount

    expires_at = timezone.now() + timedelta(hours=24)
    provider = get_payment_provider(provider_name)

    va_number = None
    va_bank = None
    qris_payload = None

    if method == PaymentMethod.VA:
        va_bank = (bank or 'BCA').upper()
        # Per FIN-011: use stable per-student VA if supported
        va_obj = get_or_create_student_va(student=student, school=school, bank=va_bank, provider_name=provider_name)
        va_number = va_obj.va_number
    elif method == PaymentMethod.QRIS:
        qr_data = provider.create_qris(student=student, school=school, amount=intent_amount, expires_at=expires_at)
        qris_payload = qr_data.get('qris_payload')

    intent = PaymentIntent.objects.create(
        foundation_id=school.foundation_id,
        school=school,
        student=student,
        invoice=invoices[0] if len(invoices) == 1 else None,
        method=method,
        provider=provider_name.upper(),
        va_bank=va_bank,
        va_number=va_number,
        qris_payload=qris_payload,
        amount=intent_amount,
        currency=currency,
        expires_at=expires_at,
        status=PaymentIntentStatus.PENDING,
        metadata={
            'invoice_ids': [inv.id for inv in invoices],
            'base_amount': str(base_amount),
            'convenience_fee_amount': str(convenience_fee_amount),
        },
    )
    return intent


@transaction.atomic
def allocate_payment_to_invoices(
    payment: Payment,
    target_invoices: list[Invoice] = None,
) -> tuple[list[PaymentAllocation], Decimal]:
    """
    FIN-014: Allocate payment across invoices, default oldest-first.
    FIN-015: Overpayment creates student credit balance.
    FIN-016: Underpayment transitions invoice to PARTIALLY_PAID.
    """
    if target_invoices is None:
        # Default allocation: oldest unpaid invoices first (FIN-014)
        target_invoices = list(
            Invoice.objects.select_for_update().filter(
                foundation_id=payment.foundation_id,
                school=payment.school,
                student=payment.student,
                currency=payment.currency,
            ).exclude(
                status__in=[InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF]
            ).order_by('issue_date', 'due_date', 'id')
        )
    else:
        # Re-fetch with lock
        target_invoices = list(
            Invoice.objects.select_for_update().filter(
                id__in=[inv.id for inv in target_invoices],
                foundation_id=payment.foundation_id,
            ).order_by('issue_date', 'due_date', 'id')
        )

    # Validate currencies match
    for inv in target_invoices:
        if inv.currency != payment.currency:
            raise CurrencyMismatchError(
                f"CURRENCY_MISMATCH: Invoice currency {inv.currency} != Payment currency {payment.currency}"
            )

    remaining = payment.amount
    allocations = []

    for invoice in target_invoices:
        if remaining <= Decimal('0.00'):
            break

        balance_due = invoice.balance_due
        if balance_due <= Decimal('0.00'):
            continue

        alloc_amount = min(remaining, balance_due)
        allocation = PaymentAllocation.objects.create(
            foundation_id=payment.foundation_id,
            payment=payment,
            invoice=invoice,
            amount=alloc_amount,
            currency=payment.currency,
        )
        allocations.append(allocation)

        # Allocate to active installments if any (FIN-030)
        from apps.finance.services.installments import allocate_payment_to_installments
        allocate_payment_to_installments(
            invoice=invoice,
            payment_amount=alloc_amount,
            paid_at=payment.settled_at or timezone.now(),
        )

        invoice.paid += alloc_amount
        if invoice.paid >= invoice.total:
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        invoice.save(update_fields=['paid', 'status', 'updated_at'])

        remaining -= alloc_amount

    overpayment = max(Decimal('0.00'), remaining)

    # FIN-015: Overpayment creates credit balance on the student
    if overpayment > Decimal('0.00'):
        credit, _ = StudentCreditBalance.objects.select_for_update().get_or_create(
            foundation_id=payment.foundation_id,
            student=payment.student,
            currency=payment.currency,
            defaults={'balance': Decimal('0.00')},
        )
        credit.balance += overpayment
        credit.save(update_fields=['balance', 'updated_at'])

    return allocations, overpayment


def _dispatch_payment_received_notification(payment: Payment, allocations: list) -> None:
    """
    PAR-008: Dispatch PAYMENT_RECEIVED push notification to guardians
    within 30s of settlement. Dispatched via all three settlement paths:
    gateway webhook, cash payment, and manual transfer verification.
    """
    try:
        from apps.notifications.services import dispatch_intent
        from apps.notifications.models import NotificationCategory, NotificationPriority
        from apps.identity.models import GuardianLink
        from django.db.models import Q as Q_

        student = payment.student
        if not student:
            return

        guardian_links = list(
            GuardianLink.objects.filter(
                foundation_id=payment.foundation_id,
                student=student,
                deleted_at__isnull=True,
            )
            .filter(Q_(is_primary=True) | Q_(financial_responsible=True))
            .select_related('guardian__user', 'guardian__person')
        )
        if not guardian_links:
            guardian_links = list(
                GuardianLink.objects.filter(
                    foundation_id=payment.foundation_id,
                    student=student,
                    deleted_at__isnull=True,
                )
                .select_related('guardian__user', 'guardian__person')
            )

        # Build invoice summary
        invoice_numbers = [a.invoice.number for a in allocations if a.invoice]
        invoice_info = ', '.join(invoice_numbers[:3])
        if len(invoice_numbers) > 3:
            invoice_info += f' (+{len(invoice_numbers) - 3} lainnya)'

        student_name = student.person.full_name if student.person else ''

        for link in guardian_links:
            guardian = link.guardian
            if not guardian:
                continue
            guardian_user = guardian.user
            if not guardian_user:
                continue
            guardian_person = guardian.person
            phone = guardian_user.phone_e164 if guardian_user.phone_e164 else ''
            name = guardian_person.full_name if guardian_person else ''

            dispatch_intent(
                foundation_id=payment.foundation_id,
                category=NotificationCategory.PAYMENT_RECEIVED,
                template_key='finance.payment_received',
                school_id=payment.school_id,
                recipient_user=guardian_user,
                recipient_phone=phone,
                recipient_name=name,
                payload={
                    'student_name': student_name,
                    'amount': str(payment.amount),
                    'currency': payment.currency,
                    'invoice_info': invoice_info,
                    'payment_reference': payment.reference,
                },
                priority=NotificationPriority.HIGH,
                dedupe_key=f"payment_received:{payment.id}:{guardian.id}",
                immediate=True,
            )
    except Exception as e:
        logger.warning("Failed to dispatch payment-received notification for payment %s: %s", payment.id, e)


@transaction.atomic
def process_payment_webhook(
    provider_name: str,
    payload: dict,
    headers: dict = None,
) -> dict:
    """
    FIN-013: Process gateway payment webhooks.
    - Verifies webhook signature.
    - Idempotent on external_id (duplicate returns 200 without double-settlement).
    - Out-of-order handling (settlement arriving before authorization).
    - Posts balanced double-entry ledger journal.
    """
    provider = get_payment_provider(provider_name)
    if not provider.verify_webhook(payload, headers):
        raise ValueError("Invalid webhook signature")

    parsed = provider.parse_webhook(payload)
    external_id = parsed['external_id']
    amount = parsed['amount']
    fee = parsed['fee']
    net = parsed['net']
    status = parsed['status']
    channel = parsed['channel']

    # 1. Check idempotency on external_id (FIN-013)
    existing_payment = Payment.all_tenants.filter(external_id=external_id).first()
    if existing_payment:
        if existing_payment.status == PaymentStatus.SETTLED:
            return {
                'status': 'already_settled',
                'payment_id': existing_payment.id,
                'reference': existing_payment.reference,
            }
        payment = existing_payment
        foundation_id = payment.foundation_id
        student = payment.student
        school = payment.school
        intent = payment.payment_intent
    else:
        # Out-of-order webhook or direct payment
        # Attempt to locate intent or student from metadata/order_id
        intent = None
        student = None
        school = None

        # Look up by intent id or metadata
        if external_id.startswith('INT-') or external_id.isdigit():
            clean_id = external_id.replace('INT-', '')
            intent = PaymentIntent.all_tenants.filter(id=clean_id).first()

        raw_data = parsed.get('raw', {})
        if not intent and 'metadata' in raw_data and isinstance(raw_data['metadata'], dict):
            intent_id = raw_data['metadata'].get('intent_id')
            if intent_id:
                intent = PaymentIntent.all_tenants.filter(id=intent_id).first()

        if intent:
            student = intent.student
            school = intent.school
            foundation_id = intent.foundation_id
        else:
            # Fallback: resolve from VA number or payload student_id / school_id
            va_numbers = raw_data.get('va_numbers', [])
            va_number = va_numbers[0].get('va_number') if va_numbers else None
            va_obj = StudentVirtualAccount.all_tenants.filter(va_number=va_number).first() if va_number else None
            if va_obj:
                student = va_obj.student
                school = student.school
                foundation_id = va_obj.foundation_id
            else:
                target_student_id = raw_data.get('student_id')
                if target_student_id:
                    student = Student.all_tenants.filter(id=target_student_id).first()
                if not student:
                    student = Student.all_tenants.filter(status=Student.STATUS_ACTIVE).first()
                school = student.school if student else School.all_tenants.first()
                foundation_id = school.foundation_id if school else 1

    from educore.middleware.tenancy import tenant_context
    with tenant_context(foundation_id):
        if not existing_payment:
            reference = get_next_payment_reference(school)
            payment = Payment.objects.create(
                foundation_id=foundation_id,
                school=school,
                student=student,
                payment_intent=intent,
                amount=amount,
                fee=fee,
                net=net,
                currency='IDR',
                method=PaymentMethod.VA if 'VA' in channel else PaymentMethod.QRIS,
                channel=channel,
                reference=reference,
                external_id=external_id,
                status=PaymentStatus.PENDING,
                paid_at=parsed['paid_at'],
                metadata=parsed['raw'],
            )

        if status == 'SETTLED':
            payment.status = PaymentStatus.SETTLED
            payment.settled_at = timezone.now()
            payment.fee = fee
            payment.net = net
            payment.save(update_fields=['status', 'settled_at', 'fee', 'net', 'updated_at'])

            if payment.payment_intent:
                payment.payment_intent.status = PaymentIntentStatus.COMPLETED
                payment.payment_intent.save(update_fields=['status', 'updated_at'])

            # Allocate to invoices
            allocations, overpayment = allocate_payment_to_invoices(payment)

            # Post balanced double-entry ledger journal (FIN-021, FIN-023, CUR-020)
            post_payment_settlement_journal(
                payment=payment,
                allocations=allocations,
                overpayment=overpayment,
            )

            # Record domain event
            record_domain_event(
                name='finance.payment_settled',
                payload={
                    'payment_id': payment.id,
                    'reference': payment.reference,
                    'student_id': payment.student.id if payment.student else None,
                    'amount': str(payment.amount),
                    'currency': payment.currency,
                    'channel': payment.channel,
                },
                foundation_id=payment.foundation_id,
            )

            # PAR-008: Dispatch push notification to guardians
            _dispatch_payment_received_notification(payment, allocations)

            audit(
                action='finance.payment.settled',
                entity_type='Payment',
                entity_id=payment.id,
                foundation_id=payment.foundation_id,
                school_id=payment.school.id if payment.school else None,
                role='GATEWAY_WEBHOOK',
                diff={
                    'external_id': external_id,
                    'amount': str(payment.amount),
                    'status': 'SETTLED',
                },
            )

            return {
                'status': 'settled',
                'payment_id': payment.id,
                'reference': payment.reference,
                'allocated_invoices': len(allocations),
                'overpayment': str(overpayment),
            }

        return {
            'status': payment.status,
            'payment_id': payment.id,
            'reference': payment.reference,
        }



@transaction.atomic
def record_cash_payment(
    school: School,
    student: Student,
    amount: Decimal,
    invoice_ids: list[int] = None,
    received_by=None,
    notes: str = '',
) -> Payment:
    """
    FIN-019: Cash payment desk receipt generation with unique receipt number and receiving officer.
    """
    if amount <= Decimal('0.00'):
        raise InvalidPaymentError("Cash payment amount must be positive.")

    reference = get_next_payment_reference(school)
    receipt_number = get_next_receipt_number(school)
    now = timezone.now()

    payment = Payment.objects.create(
        foundation_id=school.foundation_id,
        school=school,
        student=student,
        amount=amount,
        fee=Decimal('0.00'),
        net=amount,
        currency=school.base_currency or 'IDR',
        method=PaymentMethod.CASH,
        channel='CASHIER',
        reference=reference,
        receipt_number=receipt_number,
        received_by=received_by,
        status=PaymentStatus.SETTLED,
        paid_at=now,
        settled_at=now,
        metadata={'notes': notes},
    )

    target_invoices = None
    if invoice_ids:
        target_invoices = list(Invoice.objects.filter(id__in=invoice_ids, foundation_id=school.foundation_id))

    allocations, overpayment = allocate_payment_to_invoices(payment, target_invoices=target_invoices)

    # Post balanced ledger journal (FIN-021, FIN-023)
    post_payment_settlement_journal(
        payment=payment,
        allocations=allocations,
        overpayment=overpayment,
    )

    record_domain_event(
        name='finance.cash_payment_received',
        payload={
            'payment_id': payment.id,
            'reference': payment.reference,
            'receipt_number': payment.receipt_number,
            'student_id': student.id,
            'amount': str(payment.amount),
        },
        foundation_id=school.foundation_id,
    )

    # PAR-008: Dispatch push notification to guardians
    _dispatch_payment_received_notification(payment, allocations)

    audit(
        action='finance.payment.cash_received',
        entity_type='Payment',
        entity_id=payment.id,
        actor_id=str(received_by.id) if received_by else None,
        foundation_id=school.foundation_id,
        school_id=school.id,
        role='STAFF',
        diff={
            'receipt_number': receipt_number,
            'amount': str(amount),
            'status': 'SETTLED',
        },
    )


    return payment


@transaction.atomic
def submit_manual_transfer(
    school: School,
    student: Student,
    amount: Decimal,
    invoice_ids: list[int] = None,
    proof_file: str = '',
    channel: str = 'MANUAL_TRANSFER',
    notes: str = '',
) -> Payment:
    """
    FIN-018: Manual transfer enters PENDING_VERIFICATION.
    """
    if amount <= Decimal('0.00'):
        raise InvalidPaymentError("Transfer amount must be positive.")

    reference = get_next_payment_reference(school)
    payment = Payment.objects.create(
        foundation_id=school.foundation_id,
        school=school,
        student=student,
        amount=amount,
        fee=Decimal('0.00'),
        net=amount,
        currency=school.base_currency or 'IDR',
        method=PaymentMethod.MANUAL,
        channel=channel,
        reference=reference,
        proof_file=proof_file,
        status=PaymentStatus.PENDING_VERIFICATION,
        paid_at=timezone.now(),
        metadata={'notes': notes, 'target_invoice_ids': invoice_ids or []},
    )
    return payment


@transaction.atomic
def verify_manual_transfer(
    payment: Payment,
    verified_by,
    decision: str,
    reason: str = '',
) -> Payment:
    """
    FIN-018: Finance officer approves or rejects manual transfer.
    """
    if payment.status != PaymentStatus.PENDING_VERIFICATION:
        raise InvalidPaymentError(f"Cannot verify payment with status '{payment.status}'.")

    decision_upper = decision.upper()
    now = timezone.now()

    if decision_upper == 'APPROVE':
        payment.status = PaymentStatus.SETTLED
        payment.settled_at = now
        payment.receipt_number = get_next_receipt_number(payment.school)
        payment.received_by = verified_by
        payment.save(update_fields=['status', 'settled_at', 'receipt_number', 'received_by', 'updated_at'])

        # Resolve target invoices if provided
        target_invoice_ids = payment.metadata.get('target_invoice_ids', [])
        target_invoices = None
        if target_invoice_ids:
            target_invoices = list(Invoice.objects.filter(id__in=target_invoice_ids, foundation_id=payment.foundation_id))

        allocations, overpayment = allocate_payment_to_invoices(payment, target_invoices=target_invoices)

        # Post balanced ledger journal
        post_payment_settlement_journal(
            payment=payment,
            allocations=allocations,
            overpayment=overpayment,
        )

        record_domain_event(
            name='finance.manual_transfer_approved',
            payload={
                'payment_id': payment.id,
                'reference': payment.reference,
                'receipt_number': payment.receipt_number,
                'student_id': payment.student.id,
                'amount': str(payment.amount),
            },
            foundation_id=payment.foundation_id,
        )

        # PAR-008: Dispatch push notification to guardians
        _dispatch_payment_received_notification(payment, allocations)

        audit(
            action='finance.manual_transfer.approved',
            entity_type='Payment',
            entity_id=payment.id,
            actor_id=str(verified_by.id) if verified_by else None,
            foundation_id=payment.foundation_id,
            school_id=payment.school.id if payment.school else None,
            role='FINANCE_OFFICER',
            diff={'status': 'SETTLED', 'receipt_number': payment.receipt_number},
        )
    elif decision_upper == 'REJECT':
        payment.status = PaymentStatus.REJECTED
        payment.metadata['rejection_reason'] = reason
        payment.save(update_fields=['status', 'metadata', 'updated_at'])

        audit(
            action='finance.manual_transfer.rejected',
            entity_type='Payment',
            entity_id=payment.id,
            actor_id=str(verified_by.id) if verified_by else None,
            foundation_id=payment.foundation_id,
            school_id=payment.school.id if payment.school else None,
            role='FINANCE_OFFICER',
            diff={'status': 'REJECTED', 'reason': reason},
        )
    else:
        raise ValueError("Decision must be 'APPROVE' or 'REJECT'.")


    return payment

import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.finance.models import (
    DEFAULT_ARREARS_LADDER_DAYS,
    Invoice,
    InvoiceInstallment,
    InvoiceInstallmentStatus,
    InvoiceStatus,
    SchoolArrearsPolicy,
)
from apps.identity.models import GuardianLink, School
from apps.notifications.models import (
    NotificationCategory,
    NotificationIntent,
    NotificationPriority,
)
from apps.notifications.services import dispatch_intent

logger = logging.getLogger(__name__)


def get_school_arrears_policy(school: School) -> SchoolArrearsPolicy:
    """Retrieve school arrears ladder configuration or a transient default instance."""
    policy = SchoolArrearsPolicy.objects.filter(school=school, deleted_at__isnull=True).first()
    if not policy:
        policy = SchoolArrearsPolicy(
            foundation_id=school.foundation_id,
            school=school,
            ladder_days=DEFAULT_ARREARS_LADDER_DAYS,
            is_active=True,
            payment_deep_link_base="/pay/{invoice_id}",
        )
    return policy


def is_invoice_reminder_still_needed(intent: NotificationIntent) -> bool:
    """Send-time condition re-evaluator for PAYMENT_DUE notifications (FIN-027, NTF-004).
    Called by apps.notifications.services.process_intent right before sending.
    If the invoice was settled, paid in full, cancelled, or written off between
    scheduling and delivery, this returns False, which cancels the intent with
    'Tagihan telah lunas sebelum pengingat terkirim (FIN-027)'.
    """
    invoice_id = intent.payload.get('invoice_id')
    if not invoice_id:
        return True  # Fail-open if not invoice-specific

    invoice = Invoice.all_tenants.filter(id=invoice_id, deleted_at__isnull=True).first()
    if not invoice:
        return False

    # Fully paid, zero balance, or terminal non-billable states cancel the reminder
    if invoice.status in [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF]:
        return False

    if invoice.balance_due <= Decimal('0.00'):
        return False

    # If this reminder was for a specific installment (FIN-030)
    installment_no = intent.payload.get('installment_no')
    if installment_no is not None:
        inst = InvoiceInstallment.all_tenants.filter(
            invoice=invoice,
            installment_no=installment_no,
            deleted_at__isnull=True,
        ).first()
        if not inst:
            return False
        if inst.status in [InvoiceInstallmentStatus.PAID, InvoiceInstallmentStatus.CANCELLED]:
            return False
        if inst.balance_due <= Decimal('0.00'):
            return False

    return True


def evaluate_invoice_arrears(
    invoice: Invoice,
    as_of_date: datetime.date,
    ladder_days: List[int],
) -> Optional[int]:
    """Check whether the invoice due date relative to as_of_date matches a ladder offset."""
    if invoice.status not in [InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID]:
        return None

    if invoice.balance_due <= Decimal('0.00'):
        return None

    days_diff = (as_of_date - invoice.due_date).days
    if days_diff in ladder_days:
        return days_diff

    return None


def evaluate_installment_arrears(
    installment: InvoiceInstallment,
    as_of_date: datetime.date,
    ladder_days: List[int],
) -> Optional[int]:
    """Check whether the installment due date relative to as_of_date matches a ladder offset (FIN-030)."""
    if installment.status not in [InvoiceInstallmentStatus.PENDING, InvoiceInstallmentStatus.PARTIALLY_PAID]:
        return None

    if installment.balance_due <= Decimal('0.00'):
        return None

    days_diff = (as_of_date - installment.due_date).days
    if days_diff in ladder_days:
        return days_diff

    return None


def run_arrears_ladder(
    school: School,
    as_of_date: Optional[datetime.date] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute the automated arrears reminder ladder for a school (spec/06 §6, FIN-026, FIN-030).
    
    Evaluates unpaid invoices or active installments whose due date matches any ladder offset
    (e.g. T-3, T-0, T+3, T+7, T+14, T+30), resolves primary/financially responsible guardians,
    and queues PAYMENT_DUE notification intents.
    """
    if as_of_date is None:
        as_of_date = timezone.localdate()

    policy = get_school_arrears_policy(school)
    if not policy.is_active:
        return {
            'school_id': school.id,
            'school_name': school.name,
            'is_active': False,
            'as_of_date': str(as_of_date),
            'evaluated_count': 0,
            'eligible_count': 0,
            'dispatched_count': 0,
            'skipped_existing_count': 0,
            'reminders': [],
        }

    ladder_days = policy.get_effective_ladder_days()
    deep_link_base = policy.payment_deep_link_base or "/pay/{invoice_id}"

    invoices = Invoice.objects.filter(
        school=school,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
        deleted_at__isnull=True,
    ).select_related('student', 'student__person').prefetch_related('installments')

    evaluated_count = 0
    eligible_count = 0
    dispatched_count = 0
    skipped_existing_count = 0
    reminders: List[Dict[str, Any]] = []

    for invoice in invoices:
        evaluated_count += 1

        # Check if invoice has active installments (FIN-030)
        active_installments = [
            inst for inst in invoice.installments.all()
            if inst.status in [InvoiceInstallmentStatus.PENDING, InvoiceInstallmentStatus.PARTIALLY_PAID]
            and inst.deleted_at is None
        ]
        # Sort by due_date, installment_no
        active_installments.sort(key=lambda x: (x.due_date, x.installment_no))

        items_to_remind = []
        if active_installments:
            for inst in active_installments:
                days_diff = evaluate_installment_arrears(inst, as_of_date, ladder_days)
                if days_diff is not None:
                    step_label = f"T{days_diff:+d}" if days_diff != 0 else "T-0"
                    amount_str = f"Rp {inst.balance_due:,.0f}".replace(',', '.') if invoice.currency == 'IDR' else f"{invoice.currency} {inst.balance_due:,.2f}"
                    items_to_remind.append({
                        'days_diff': days_diff,
                        'step_label': step_label,
                        'amount_str': amount_str,
                        'balance_due': inst.balance_due,
                        'due_date': inst.due_date,
                        'installment_no': inst.installment_no,
                        'dedupe_key': f"arrears_reminder:{invoice.id}:inst{inst.installment_no}:{days_diff}",
                    })
        else:
            days_diff = evaluate_invoice_arrears(invoice, as_of_date, ladder_days)
            if days_diff is not None:
                step_label = f"T{days_diff:+d}" if days_diff != 0 else "T-0"
                amount_str = f"Rp {invoice.balance_due:,.0f}".replace(',', '.') if invoice.currency == 'IDR' else f"{invoice.currency} {invoice.balance_due:,.2f}"
                items_to_remind.append({
                    'days_diff': days_diff,
                    'step_label': step_label,
                    'amount_str': amount_str,
                    'balance_due': invoice.balance_due,
                    'due_date': invoice.due_date,
                    'installment_no': None,
                    'dedupe_key': f"arrears_reminder:{invoice.id}:{days_diff}",
                })

        if not items_to_remind:
            continue

        eligible_count += 1
        deep_link = deep_link_base.format(invoice_id=invoice.id)

        # Resolve recipient guardians: prioritize financial_responsible, then is_primary
        guardian_links = list(
            GuardianLink.objects.filter(
                student=invoice.student,
                deleted_at__isnull=True,
            ).select_related('guardian', 'guardian__person', 'guardian__user')
        )

        target_links = [gl for gl in guardian_links if gl.financial_responsible]
        if not target_links:
            target_links = [gl for gl in guardian_links if gl.is_primary]
        if not target_links:
            target_links = guardian_links

        # Deduplicate guardians for the same invoice
        seen_guardian_ids = set()
        recipients = []
        for gl in target_links:
            guardian = gl.guardian
            if guardian.id in seen_guardian_ids:
                continue
            seen_guardian_ids.add(guardian.id)
            recipients.append(guardian)

        for item in items_to_remind:
            days_diff = item['days_diff']
            step_label = item['step_label']
            amount_str = item['amount_str']
            dedupe_key = item['dedupe_key']

            payload = {
                'student_name': invoice.student.person.full_name,
                'period': invoice.period,
                'amount': amount_str,
                'balance_due': str(item['balance_due']),
                'due_date': str(item['due_date']),
                'deep_link': deep_link,
                'invoice_number': invoice.number,
                'invoice_id': invoice.id,
                'days_diff': days_diff,
                'step_label': step_label,
            }
            if item['installment_no'] is not None:
                payload['installment_no'] = item['installment_no']

            for guardian in recipients:
                recipient_user = guardian.user
                recipient_phone = guardian.user.phone_e164 if guardian.user else ''
                recipient_email = guardian.user.email if guardian.user else ''
                recipient_name = guardian.person.full_name

                reminder_item = {
                    'invoice_id': invoice.id,
                    'invoice_number': invoice.number,
                    'student_name': invoice.student.person.full_name,
                    'guardian_name': recipient_name,
                    'days_diff': days_diff,
                    'step_label': step_label,
                    'amount': amount_str,
                    'deep_link': deep_link,
                    'dedupe_key': dedupe_key,
                }
                if item['installment_no'] is not None:
                    reminder_item['installment_no'] = item['installment_no']

                if dry_run:
                    reminders.append(reminder_item)
                    continue

                full_dedupe_key = f"{dedupe_key}:{guardian.id}"
                if NotificationIntent.objects.filter(
                    foundation_id=school.foundation_id,
                    dedupe_key=full_dedupe_key,
                    deleted_at__isnull=True,
                ).exists():
                    skipped_existing_count += 1
                    continue

                try:
                    intent = dispatch_intent(
                        foundation_id=school.foundation_id,
                        school_id=school.id,
                        category=NotificationCategory.PAYMENT_DUE,
                        template_key='finance.payment_due',
                        payload=payload,
                        recipient_user=recipient_user,
                        recipient_phone=recipient_phone,
                        recipient_email=recipient_email,
                        recipient_name=recipient_name,
                        dedupe_key=full_dedupe_key,
                        priority=NotificationPriority.NORMAL,
                    )
                    dispatched_count += 1
                    reminders.append(reminder_item)
                except Exception as e:
                    logger.error(f"Failed to dispatch arrears reminder for invoice {invoice.number}: {e}")

    return {
        'school_id': school.id,
        'school_name': school.name,
        'is_active': True,
        'as_of_date': str(as_of_date),
        'evaluated_count': evaluated_count,
        'eligible_count': eligible_count,
        'dispatched_count': dispatched_count,
        'skipped_existing_count': skipped_existing_count,
        'reminders': reminders,
    }

"""Foundation Approvals Inbox: a unified read/decide layer over the
per-domain approval workflows already implemented in apps.finance
(spec/03 §2/§5, FND-007/008/009).

This module never gets imported by apps.finance — it lazily imports
apps.finance from inside each function body, the same leaf-to-leaf pattern
apps.academic already uses to call into apps.finance (see
apps/academic/services.py's arrears-gate lookup).

Decisions on scope, made because the underlying data model doesn't carry a
literal "discount vs waiver" distinction (Discount has no such field -- see
apps/finance/models.py's Discount, whose own docstring already calls it
"Discount or waiver rule"):
  - A Discount row is reported (and filterable) as approval_type='waiver'
    when its `type` is FIXED (forgiving a fixed amount), and 'discount'
    when PERCENT. This gives the spec's `type=discount|waiver` filter a
    real, distinct meaning instead of duplicating identical rows under
    both labels.
  - `type=payroll` is a recognized filter value that always returns an
    empty list: no payroll domain model exists anywhere in this codebase
    yet (see the sibling Notion Open Item logged for that gap).
  - InvoiceWriteOffRequest (bad debt write-offs) is NOT included: spec/03's
    Approvals Inbox screen and FND-007's `type` enum both name only
    discount/waiver/refund/payroll, and write-offs already have their own
    dedicated apps.finance endpoint (InvoiceWriteOffRequestViewSet).
  - Each item's `id` is a composite `"<type>:<pk>"` string (e.g.
    "discount:42"), since the four approval kinds are independent models
    with independently-numbering primary keys -- a bare numeric :id in
    POST /foundation/approvals/:id/decide would be ambiguous.
"""
import logging

from django.core.exceptions import PermissionDenied, ValidationError

logger = logging.getLogger(__name__)

APPROVAL_TYPE_DISCOUNT = 'discount'
APPROVAL_TYPE_WAIVER = 'waiver'
APPROVAL_TYPE_REFUND = 'refund'
APPROVAL_TYPE_PAYROLL = 'payroll'
APPROVAL_TYPES = (APPROVAL_TYPE_DISCOUNT, APPROVAL_TYPE_WAIVER, APPROVAL_TYPE_REFUND, APPROVAL_TYPE_PAYROLL)

# Bounds each backing queryset in list_foundation_approvals -- a governance
# inbox has no cursor pagination (spec/03 §5 doesn't specify one here, unlike
# the audit explorer), so this caps worst-case query/serialization cost
# instead of loading a foundation's entire multi-year approval history.
LIST_LIMIT_PER_TYPE = 200


class ApprovalNotFoundError(LookupError):
    """Raised when a composite approval id doesn't resolve to a real row."""


class InvalidApprovalIdError(ValueError):
    """Raised when an approval id isn't a well-formed '<type>:<pk>' string."""


def _resolve_status_filter(status):
    if not status:
        return None
    upper = status.upper()
    return 'PENDING_APPROVAL' if upper == 'PENDING' else upper


def _discount_approval_type(discount):
    from apps.finance.models import DiscountType

    return APPROVAL_TYPE_WAIVER if discount.type == DiscountType.FIXED else APPROVAL_TYPE_DISCOUNT


def _serialize_discount(discount):
    from apps.finance.models import DiscountType

    approval_type = _discount_approval_type(discount)
    return {
        'id': f'{approval_type}:{discount.id}',
        'type': approval_type,
        'status': discount.status,
        'school_id': discount.student.school_id,
        'student_id': discount.student_id,
        'student_name': discount.student.person.full_name,
        'amount': str(discount.value),
        'currency': discount.student.school.base_currency if discount.type == DiscountType.FIXED else None,
        'reason': discount.reason,
        'requested_by': discount.created_by or '',
        'approved_by_id': discount.approved_by_id,
        'decided_at': discount.approved_at.isoformat() if discount.approved_at else None,
        'requested_at': discount.created_at.isoformat(),
    }


def _serialize_refund(refund):
    return {
        'id': f'refund:{refund.id}',
        'type': APPROVAL_TYPE_REFUND,
        'status': refund.status,
        'school_id': refund.school_id,
        'student_id': refund.student_id,
        'student_name': refund.student.person.full_name,
        'amount': str(refund.amount),
        'currency': refund.currency,
        'reason': refund.reason,
        'requested_by': str(refund.requested_by_id) if refund.requested_by_id else '',
        'approved_by_id': refund.approved_by_id,
        'decided_at': refund.approved_at.isoformat() if refund.approved_at else None,
        'requested_at': refund.created_at.isoformat(),
    }


def list_foundation_approvals(foundation_id, status=None, approval_type=None):
    """FND-007/008/009: unified pending/decided approvals across discount,
    waiver, refund, and (always-empty, see module docstring) payroll."""
    status_filter = _resolve_status_filter(status)
    items = []

    if approval_type in (None, APPROVAL_TYPE_DISCOUNT, APPROVAL_TYPE_WAIVER):
        from apps.finance.models import Discount, DiscountType

        qs = Discount.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person', 'student__school')
        if approval_type == APPROVAL_TYPE_DISCOUNT:
            qs = qs.filter(type=DiscountType.PERCENT)
        elif approval_type == APPROVAL_TYPE_WAIVER:
            qs = qs.filter(type=DiscountType.FIXED)
        if status_filter:
            qs = qs.filter(status=status_filter)
        qs = qs.order_by('-created_at')[:LIST_LIMIT_PER_TYPE]
        items.extend(_serialize_discount(d) for d in qs)

    if approval_type in (None, APPROVAL_TYPE_REFUND):
        from apps.finance.models import Refund

        qs = Refund.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person')
        if status_filter:
            qs = qs.filter(status=status_filter)
        qs = qs.order_by('-created_at')[:LIST_LIMIT_PER_TYPE]
        items.extend(_serialize_refund(r) for r in qs)

    # APPROVAL_TYPE_PAYROLL: no payroll domain model exists yet (see module
    # docstring) -- recognized filter value, always contributes zero rows.

    items.sort(key=lambda item: item['requested_at'], reverse=True)
    return items


def decide_foundation_approval(approval_id, foundation_id, user, decision, reason=''):
    """FND-008/009: approve or reject a single approval by its composite id.
    Delegates to the existing per-domain service functions (approve_discount/
    reject_discount, approve_refund) so the actual authorization (foundation
    admin only), state transitions, and audit trail stay in one place each."""
    kind, _sep, pk = approval_id.partition(':')
    if not _sep or not pk.isdigit():
        raise InvalidApprovalIdError(
            f"'{approval_id}' bukan id persetujuan yang valid (format: 'tipe:id')."
        )

    decision_upper = (decision or '').upper().strip()
    if decision_upper not in ('APPROVE', 'REJECT'):
        raise ValidationError("Keputusan harus 'APPROVE' atau 'REJECT'.")

    if kind in (APPROVAL_TYPE_DISCOUNT, APPROVAL_TYPE_WAIVER):
        from apps.finance.models import Discount
        from apps.finance.services.invoicing import approve_discount, reject_discount

        discount = Discount.objects.filter(id=pk, foundation_id=foundation_id, deleted_at__isnull=True).first()
        if not discount or _discount_approval_type(discount) != kind:
            # A stale/guessed id whose prefix doesn't match this row's actual
            # PERCENT/FIXED type is treated as not found, not silently decided
            # under the wrong label (a real gap an earlier review caught).
            raise ApprovalNotFoundError(approval_id)
        if decision_upper == 'APPROVE':
            result = approve_discount(discount, user)
        else:
            result = reject_discount(discount, user, reason=reason)
        return _serialize_discount(result)

    if kind == APPROVAL_TYPE_REFUND:
        from apps.finance.models import Refund
        from apps.finance.services.refunds import approve_refund
        from educore.middleware.tenancy import tenant_context

        refund = Refund.objects.filter(id=pk, foundation_id=foundation_id, deleted_at__isnull=True).first()
        if not refund:
            raise ApprovalNotFoundError(approval_id)
        with tenant_context(foundation_id):
            result = approve_refund(refund, user, decision=decision_upper, reason=reason)
        return _serialize_refund(result)

    if kind == APPROVAL_TYPE_PAYROLL:
        raise ApprovalNotFoundError(approval_id)

    raise InvalidApprovalIdError(f"Tipe persetujuan '{kind}' tidak dikenali.")

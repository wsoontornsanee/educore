import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.core.services import audit
from apps.wallet.models import (
    MerchantSettlement,
    MerchantSettlementStatus,
    POSTransaction,
    POSTransactionStatus,
    Product,
    SpendRule,
    Wallet,
    WalletReconciliation,
    WalletReconciliationStatus,
    WalletReconciliationTrigger,
    WalletStatus,
    WalletTransaction,
    WalletTransactionStatus,
    WalletTransactionType,
)


class InsufficientBalanceError(ValueError):
    pass


class WalletNotActiveError(ValueError):
    pass


class CurrencyMismatchError(ValueError):
    pass


class SpendNotAllowedError(ValueError):
    pass


class VoidWindowExpiredError(ValueError):
    pass


class SettlementStateError(ValueError):
    pass


def get_or_create_wallet(student) -> Wallet:
    wallet, _created = Wallet.objects.get_or_create(
        foundation_id=student.foundation_id,
        student=student,
        defaults={'currency': student.school.base_currency},
    )
    return wallet


def _get_locked_wallet(wallet_id, foundation_id):
    return Wallet.objects.select_for_update().get(id=wallet_id, foundation_id=foundation_id)


@transaction.atomic
def record_wallet_transaction(
    wallet: Wallet,
    tx_type: str,
    amount: Decimal,
    idempotency_key: str,
    reference='',
    currency=None,
    occurred_at=None,
    allow_negative=False,
) -> WalletTransaction:
    """WAL-001/003/004: atomic balance update + idempotent ledger insert.

    allow_negative (WAL-017): for offline-synced purchases only. An overspend is still
    ACCEPTED (never silently voided) and the wallet is flagged requires_reconciliation.
    """
    existing = WalletTransaction.objects.filter(
        foundation_id=wallet.foundation_id, wallet=wallet, idempotency_key=idempotency_key,
    ).first()
    if existing:
        return existing

    if currency and currency != wallet.currency:
        raise CurrencyMismatchError(f"CURRENCY_MISMATCH: wallet is {wallet.currency}, transaction is {currency}.")

    locked_wallet = _get_locked_wallet(wallet.id, wallet.foundation_id)

    if locked_wallet.status != WalletStatus.ACTIVE:
        raise WalletNotActiveError(f"WALLET_NOT_ACTIVE: wallet is {locked_wallet.status}.")

    new_balance = locked_wallet.balance + amount
    tx_status = WalletTransactionStatus.COMPLETED
    had_open_reconciliation = locked_wallet.requires_reconciliation
    if new_balance < Decimal('0.00'):
        if not allow_negative:
            raise InsufficientBalanceError("INSUFFICIENT_BALANCE: transaction would take the wallet negative.")
        tx_status = WalletTransactionStatus.RECONCILE_REQUIRED
        locked_wallet.requires_reconciliation = True

    locked_wallet.balance = new_balance
    locked_wallet.save(update_fields=['balance', 'requires_reconciliation', 'updated_at'])

    record = WalletTransaction.objects.create(
        foundation_id=wallet.foundation_id,
        wallet=locked_wallet,
        type=tx_type,
        amount=amount,
        balance_after=new_balance,
        reference=reference,
        occurred_at=occurred_at or timezone.now(),
        status=tx_status,
        idempotency_key=idempotency_key,
    )
    audit(
        action='wallet.transaction.recorded',
        entity_type='WalletTransaction',
        entity_id=record.id,
        foundation_id=wallet.foundation_id,
        diff={'type': tx_type, 'amount': str(amount), 'balance_after': str(new_balance), 'status': tx_status},
    )

    # REC-005/006: settlement is evaluated inside this same transaction, not by a sweep.
    if new_balance >= Decimal('0.00') and had_open_reconciliation:
        settle_reconciliations_for_wallet(locked_wallet, record)

    return record


def topup_wallet(wallet: Wallet, amount: Decimal, method: str, idempotency_key: str, reference='') -> WalletTransaction:
    """WAL-005 (manual/cash methods only this slice)."""
    if amount <= Decimal('0.00'):
        raise ValueError("INVALID_AMOUNT: top-up amount must be positive.")
    return record_wallet_transaction(
        wallet, WalletTransactionType.TOPUP, amount, idempotency_key,
        reference=reference or method,
    )


def adjust_wallet(wallet: Wallet, amount: Decimal, reason: str, idempotency_key: str, actor=None) -> WalletTransaction:
    """A signed manual adjustment (positive or negative)."""
    record = record_wallet_transaction(
        wallet, WalletTransactionType.ADJUSTMENT, amount, idempotency_key, reference=reason,
    )
    return record


def get_or_create_spend_rule(student) -> SpendRule:
    rule, _created = SpendRule.objects.get_or_create(
        foundation_id=student.foundation_id, student=student,
    )
    return rule


def set_spend_rule(
    student, daily_limit=None, blocked_categories=None, blocked_products=None,
    allowed_window_start=None, allowed_window_end=None,
) -> SpendRule:
    """WAL-009/010/011."""
    rule = get_or_create_spend_rule(student)
    rule.daily_limit = daily_limit
    rule.blocked_categories = blocked_categories or []
    rule.blocked_products = blocked_products or []
    rule.allowed_window_start = allowed_window_start
    rule.allowed_window_end = allowed_window_end
    rule.save()

    audit(
        action='wallet.spend_rule.updated',
        entity_type='SpendRule',
        entity_id=rule.id,
        foundation_id=student.foundation_id,
        diff={
            'daily_limit': str(daily_limit) if daily_limit is not None else None,
            'blocked_categories': rule.blocked_categories,
            'blocked_products': rule.blocked_products,
        },
    )
    return rule


def check_spend_allowed(wallet: Wallet, amount: Decimal, category=None, product_sku=None, at_time=None) -> dict:
    """WAL-009 to WAL-013: pre-authorisation check for a prospective purchase. Read-only, no side effects."""
    rule = SpendRule.objects.filter(foundation_id=wallet.foundation_id, student=wallet.student).first()
    if not rule:
        return {'allowed': True, 'reason': None}

    now = at_time or timezone.localtime().time()

    if rule.blocked_products and product_sku in rule.blocked_products:
        return {'allowed': False, 'reason': 'PRODUCT_BLOCKED'}

    if rule.blocked_categories and category in rule.blocked_categories:
        return {'allowed': False, 'reason': 'CATEGORY_BLOCKED'}

    if rule.allowed_window_start and rule.allowed_window_end:
        if not (rule.allowed_window_start <= now <= rule.allowed_window_end):
            return {'allowed': False, 'reason': 'OUTSIDE_ALLOWED_WINDOW'}

    if rule.daily_limit is not None:
        today = timezone.localdate()
        spent_today = WalletTransaction.objects.filter(
            foundation_id=wallet.foundation_id,
            wallet=wallet,
            type=WalletTransactionType.PURCHASE,
            status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
            occurred_at__date=today,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        # amount is expected positive here (the prospective purchase size); stored PURCHASE amounts are negative deltas.
        if (-spent_today) + amount > rule.daily_limit:
            return {'allowed': False, 'reason': 'DAILY_LIMIT_EXCEEDED'}

    return {'allowed': True, 'reason': None}


def reconcile_wallet_balances(foundation_id=None) -> dict:
    """WAL-002: recompute every wallet's balance from its transaction log and report mismatches."""
    wallets = Wallet.all_tenants.filter(deleted_at__isnull=True)
    if foundation_id:
        wallets = wallets.filter(foundation_id=foundation_id)

    checked = 0
    mismatches = []
    for wallet in wallets:
        computed = WalletTransaction.objects.filter(
            wallet=wallet,
            status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
            deleted_at__isnull=True,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        checked += 1
        if computed != wallet.balance:
            mismatches.append({'wallet_id': wallet.id, 'stored': str(wallet.balance), 'computed': str(computed)})

    if mismatches:
        audit(
            action='wallet.reconcile.mismatch_detected',
            entity_type='Wallet',
            entity_id=0,
            foundation_id=foundation_id,
            diff={'mismatches': mismatches},
        )

    return {'checked': checked, 'mismatches': mismatches}


DEFAULT_VOID_WINDOW_MINUTES = 15


def process_pos_transaction(terminal, student, items, client_transaction_id, occurred_at=None) -> POSTransaction:
    """WAL-018 to WAL-021: online checkout. Rejected sales are still logged (WAL-013)."""
    existing = POSTransaction.objects.filter(
        foundation_id=terminal.foundation_id, terminal=terminal, client_transaction_id=client_transaction_id,
    ).first()
    if existing:
        return existing

    occurred_at = occurred_at or timezone.now()
    merchant = terminal.merchant
    subtotal = sum((Decimal(str(i['unit_price'])) * i.get('qty', 1) for i in items), Decimal('0.00'))
    commission = (subtotal * merchant.commission_bps / Decimal('10000')).quantize(Decimal('0.01'))

    wallet = get_or_create_wallet(student)

    for item in items:
        check = check_spend_allowed(
            wallet, subtotal, category=item.get('category'), product_sku=item.get('sku'), at_time=occurred_at.time(),
        )
        if not check['allowed']:
            POSTransaction.objects.create(
                foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
                items=items, subtotal=subtotal, commission=commission, total=subtotal,
                occurred_at=occurred_at, status=POSTransactionStatus.REJECTED,
                client_transaction_id=client_transaction_id,
            )
            raise SpendNotAllowedError(f"SPEND_NOT_ALLOWED: {check['reason']}")

    try:
        wallet_tx = record_wallet_transaction(
            wallet, WalletTransactionType.PURCHASE, -subtotal, client_transaction_id,
            reference=f"POS:{merchant.name}", occurred_at=occurred_at,
        )
    except InsufficientBalanceError:
        POSTransaction.objects.create(
            foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
            items=items, subtotal=subtotal, commission=commission, total=subtotal,
            occurred_at=occurred_at, status=POSTransactionStatus.REJECTED,
            client_transaction_id=client_transaction_id,
        )
        raise

    pos_tx = POSTransaction.objects.create(
        foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
        items=items, subtotal=subtotal, commission=commission, total=subtotal,
        occurred_at=occurred_at, status=POSTransactionStatus.COMPLETED,
        client_transaction_id=client_transaction_id, wallet_transaction=wallet_tx,
    )
    audit(
        action='wallet.pos_transaction.completed',
        entity_type='POSTransaction',
        entity_id=pos_tx.id,
        foundation_id=terminal.foundation_id,
        diff={'merchant': merchant.name, 'total': str(subtotal)},
    )
    return pos_tx


def void_pos_transaction(pos_transaction: POSTransaction, reason: str, actor=None,
                          void_window_minutes=DEFAULT_VOID_WINDOW_MINUTES) -> POSTransaction:
    """WAL-025: operator void within the window reverses the purchase and restores balance."""
    if pos_transaction.status != POSTransactionStatus.COMPLETED:
        raise ValueError(f"INVALID_STATE: transaction is {pos_transaction.status}, not COMPLETED.")

    elapsed = timezone.now() - pos_transaction.occurred_at
    if elapsed > timedelta(minutes=void_window_minutes):
        raise VoidWindowExpiredError(
            f"VOID_WINDOW_EXPIRED: void window is {void_window_minutes} minutes."
        )

    wallet = pos_transaction.wallet_transaction.wallet
    record_wallet_transaction(
        wallet, WalletTransactionType.REFUND, pos_transaction.subtotal,
        f"void:{pos_transaction.client_transaction_id}",
        reference=f"VOID:{pos_transaction.merchant.name}",
    )

    pos_transaction.status = POSTransactionStatus.VOIDED
    pos_transaction.voided_at = timezone.now()
    pos_transaction.void_reason = reason
    pos_transaction.save(update_fields=['status', 'voided_at', 'void_reason', 'updated_at'])

    audit(
        action='wallet.pos_transaction.voided',
        entity_type='POSTransaction',
        entity_id=pos_transaction.id,
        foundation_id=pos_transaction.foundation_id,
        diff={'reason': reason, 'restored_amount': str(pos_transaction.subtotal)},
    )
    return pos_transaction


def run_merchant_settlement(merchant, period_start, period_end) -> MerchantSettlement:
    """WAL-022: gross/commission/net for a merchant over a period. Re-running while PENDING updates in place."""
    existing = MerchantSettlement.objects.filter(
        foundation_id=merchant.foundation_id, merchant=merchant,
        period_start=period_start, period_end=period_end, deleted_at__isnull=True,
    ).first()
    if existing and existing.status == MerchantSettlementStatus.PAID:
        raise SettlementStateError("SETTLEMENT_ALREADY_PAID: cannot re-run a paid settlement.")

    totals = POSTransaction.objects.filter(
        foundation_id=merchant.foundation_id, merchant=merchant,
        status=POSTransactionStatus.COMPLETED,
        occurred_at__date__gte=period_start, occurred_at__date__lte=period_end,
    ).aggregate(gross=Sum('total'), commission=Sum('commission'))

    gross = totals['gross'] or Decimal('0.00')
    commission = totals['commission'] or Decimal('0.00')
    net = gross - commission

    if existing:
        existing.gross, existing.commission, existing.net = gross, commission, net
        existing.save(update_fields=['gross', 'commission', 'net', 'updated_at'])
        settlement = existing
    else:
        settlement = MerchantSettlement.objects.create(
            foundation_id=merchant.foundation_id, merchant=merchant,
            period_start=period_start, period_end=period_end,
            gross=gross, commission=commission, net=net,
        )

    audit(
        action='wallet.merchant_settlement.run',
        entity_type='MerchantSettlement',
        entity_id=settlement.id,
        foundation_id=merchant.foundation_id,
        diff={'gross': str(gross), 'commission': str(commission), 'net': str(net)},
    )
    return settlement


def render_settlement_statement_html(settlement: MerchantSettlement) -> str:
    merchant = settlement.merchant
    return f"""<html><body>
<h1>Laporan Penyelesaian - {merchant.name}</h1>
<p>Periode: {settlement.period_start} s/d {settlement.period_end}</p>
<table border="1">
<tr><th>Bruto</th><td>{settlement.gross}</td></tr>
<tr><th>Komisi</th><td>{settlement.commission}</td></tr>
<tr><th>Neto</th><td>{settlement.net}</td></tr>
</table>
</body></html>"""


def generate_settlement_statement_pdf(settlement: MerchantSettlement) -> str:
    """Falls back to HTML if weasyprint's native libraries are unavailable in this environment."""
    html_content = render_settlement_statement_html(settlement)
    output_dir = Path(settings.MEDIA_ROOT) / 'settlements'
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from weasyprint import HTML
        key = f"settlements/{settlement.id}.pdf"
        HTML(string=html_content).write_pdf(str(Path(settings.MEDIA_ROOT) / key))
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML settlement statement", exc_info=True)
        key = f"settlements/{settlement.id}.html"
        (Path(settings.MEDIA_ROOT) / key).write_text(html_content)

    settlement.statement_pdf_key = key
    settlement.save(update_fields=['statement_pdf_key', 'updated_at'])
    return key


def mark_settlement_paid(settlement: MerchantSettlement) -> MerchantSettlement:
    if settlement.status == MerchantSettlementStatus.PAID:
        return settlement
    settlement.status = MerchantSettlementStatus.PAID
    settlement.paid_at = timezone.now()
    settlement.save(update_fields=['status', 'paid_at', 'updated_at'])
    audit(
        action='wallet.merchant_settlement.paid',
        entity_type='MerchantSettlement',
        entity_id=settlement.id,
        foundation_id=settlement.foundation_id,
    )
    return settlement


DEFAULT_OFFLINE_FLOOR_LIMIT = Decimal('50000.00')


def pos_session(terminal) -> dict:
    """POST /pos/sessions: full snapshot for offline caching (WAL-014)."""
    from apps.identity.models import Student

    students = Student.objects.filter(
        foundation_id=terminal.foundation_id, school=terminal.merchant.school,
        status=Student.STATUS_ACTIVE, wallet__isnull=False, deleted_at__isnull=True,
    ).select_related('wallet', 'person')

    return {
        'roster': _roster_payload(students),
        'catalog': _catalog_payload(terminal.merchant),
        'rules': _rules_payload(terminal.foundation_id, students),
        'cursor': timezone.now().isoformat(),
    }


def pos_sync(terminal, since_cursor=None) -> dict:
    """GET /pos/sync?cursor: incremental roster/catalog/rule deltas since a cursor (WAL-012, WAL-014)."""
    from apps.identity.models import Student

    students = Student.objects.filter(
        foundation_id=terminal.foundation_id, school=terminal.merchant.school,
        status=Student.STATUS_ACTIVE, wallet__isnull=False, deleted_at__isnull=True,
    ).select_related('wallet', 'person')

    if since_cursor:
        students = students.filter(wallet__updated_at__gt=since_cursor)

    products = Product.objects.filter(
        foundation_id=terminal.foundation_id, merchant=terminal.merchant, is_active=True, deleted_at__isnull=True,
    )
    if since_cursor:
        products = products.filter(updated_at__gt=since_cursor)

    return {
        'roster_delta': _roster_payload(students),
        'catalog_delta': [_product_dict(p) for p in products],
        'rules_delta': _rules_payload(terminal.foundation_id, students, since_cursor=since_cursor),
        'next_cursor': timezone.now().isoformat(),
    }


def _roster_payload(students) -> list:
    return [
        {
            'student_id': s.id,
            'name': s.person.full_name if s.person else '',
            'photo_key': s.photo_key,
            'wallet_balance': str(s.wallet.balance),
            'daily_limit': str(s.wallet.daily_limit) if s.wallet.daily_limit is not None else None,
            'wallet_status': s.wallet.status,
        }
        for s in students
    ]


def _product_dict(product) -> dict:
    return {
        'sku': product.sku, 'name': product.name, 'price': str(product.price),
        'category': product.category, 'is_active': product.is_active,
    }


def _catalog_payload(merchant) -> list:
    products = Product.objects.filter(foundation_id=merchant.foundation_id, merchant=merchant, is_active=True, deleted_at__isnull=True)
    return [_product_dict(p) for p in products]


def _rules_payload(foundation_id, students, since_cursor=None) -> list:
    student_ids = [s.id for s in students]
    qs = SpendRule.objects.filter(foundation_id=foundation_id, student_id__in=student_ids)
    if since_cursor:
        qs = qs.filter(updated_at__gt=since_cursor)
    return [
        {
            'student_id': r.student_id,
            'daily_limit': str(r.daily_limit) if r.daily_limit is not None else None,
            'blocked_categories': r.blocked_categories,
            'blocked_products': r.blocked_products,
            'allowed_window_start': r.allowed_window_start.isoformat() if r.allowed_window_start else None,
            'allowed_window_end': r.allowed_window_end.isoformat() if r.allowed_window_end else None,
        }
        for r in qs
    ]


def check_offline_floor(wallet: Wallet, amount: Decimal, floor_limit=DEFAULT_OFFLINE_FLOOR_LIMIT) -> bool:
    """WAL-016: cumulative offline-flagged spend per student per day must not exceed the floor."""
    today = timezone.localdate()
    spent_offline_today = POSTransaction.objects.filter(
        foundation_id=wallet.foundation_id, student=wallet.student, offline_created=True,
        status__in=[POSTransactionStatus.COMPLETED], occurred_at__date=today,
    ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
    return (spent_offline_today + amount) <= floor_limit


def process_offline_pos_batch(terminal, transactions: list) -> dict:
    """POST /pos/transactions/batch: idempotent offline sync (WAL-015, WAL-016, WAL-017)."""
    from apps.identity.models import Student

    results = []
    wallets_needing_notice = {}
    for tx_data in transactions:
        client_transaction_id = tx_data['client_transaction_id']
        existing = POSTransaction.objects.filter(
            foundation_id=terminal.foundation_id, terminal=terminal, client_transaction_id=client_transaction_id,
        ).first()
        if existing:
            results.append({'client_transaction_id': client_transaction_id, 'status': existing.status})
            continue

        student = Student.objects.filter(id=tx_data['student_id'], foundation_id=terminal.foundation_id).first()
        if not student:
            results.append({'client_transaction_id': client_transaction_id, 'status': 'STUDENT_NOT_FOUND'})
            continue

        items = tx_data['items']
        occurred_at = tx_data.get('occurred_at') or timezone.now()
        merchant = terminal.merchant
        subtotal = sum((Decimal(str(i['unit_price'])) * i.get('qty', 1) for i in items), Decimal('0.00'))
        commission = (subtotal * merchant.commission_bps / Decimal('10000')).quantize(Decimal('0.01'))
        wallet = get_or_create_wallet(student)

        if not check_offline_floor(wallet, subtotal):
            POSTransaction.objects.create(
                foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
                items=items, subtotal=subtotal, commission=commission, total=subtotal, occurred_at=occurred_at,
                status=POSTransactionStatus.REJECTED, offline_created=True, client_transaction_id=client_transaction_id,
            )
            results.append({'client_transaction_id': client_transaction_id, 'status': 'OFFLINE_FLOOR_EXCEEDED'})
            continue

        blocked = False
        for item in items:
            check = check_spend_allowed(wallet, subtotal, category=item.get('category'), product_sku=item.get('sku'))
            if not check['allowed']:
                blocked = True
                POSTransaction.objects.create(
                    foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
                    items=items, subtotal=subtotal, commission=commission, total=subtotal, occurred_at=occurred_at,
                    status=POSTransactionStatus.REJECTED, offline_created=True, client_transaction_id=client_transaction_id,
                )
                results.append({'client_transaction_id': client_transaction_id, 'status': check['reason']})
                break
        if blocked:
            continue

        # REC-001: the debit, the POS row, and the reconciliation case (if any) commit
        # or roll back together.
        with transaction.atomic():
            wallet_tx = record_wallet_transaction(
                wallet, WalletTransactionType.PURCHASE, -subtotal, client_transaction_id,
                reference=f"POS-OFFLINE:{merchant.name}", occurred_at=occurred_at, allow_negative=True,
            )
            pos_tx = POSTransaction.objects.create(
                foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal, student=student,
                items=items, subtotal=subtotal, commission=commission, total=subtotal, occurred_at=occurred_at,
                status=POSTransactionStatus.COMPLETED, offline_created=True,
                client_transaction_id=client_transaction_id, wallet_transaction=wallet_tx,
            )
            if wallet_tx.status == WalletTransactionStatus.RECONCILE_REQUIRED:
                create_reconciliation_case(wallet_tx, pos_transaction=pos_tx)
                wallets_needing_notice[wallet.id] = wallet

        results.append({'client_transaction_id': client_transaction_id, 'status': wallet_tx.status})

    for wallet in wallets_needing_notice.values():
        queue_reconciliation_notice(wallet)

    audit(
        action='wallet.pos_offline_batch.processed',
        entity_type='POSTerminal',
        entity_id=terminal.id,
        foundation_id=terminal.foundation_id,
        diff={'count': len(transactions)},
    )
    return {'results': results}


def create_reconciliation_case(wallet_tx: WalletTransaction, pos_transaction=None) -> WalletReconciliation:
    """REC-001/002: one OPEN case per accepted overspend. Caller MUST wrap this in the
    same DB transaction as the accepted wallet_tx (process_offline_pos_batch does)."""
    wallet = wallet_tx.wallet
    return WalletReconciliation.objects.create(
        foundation_id=wallet.foundation_id,
        wallet=wallet,
        student=wallet.student,
        trigger=WalletReconciliationTrigger.OFFLINE_OVERSPEND,
        shortfall=abs(wallet_tx.balance_after),
        currency=wallet.currency,
        balance_at_detection=wallet_tx.balance_after,
        pos_transaction=pos_transaction,
        detected_at=wallet_tx.occurred_at,
        detected_by_job='pos_offline_batch',
        status=WalletReconciliationStatus.OPEN,
    )


def settle_reconciliations_for_wallet(wallet: Wallet, settling_transaction: WalletTransaction) -> int:
    """REC-005/006: idempotently settle every OPEN case once the balance is >= 0."""
    open_cases = list(
        WalletReconciliation.objects.filter(
            foundation_id=wallet.foundation_id, wallet=wallet,
            status=WalletReconciliationStatus.OPEN, deleted_at__isnull=True,
        )
    )
    if not open_cases:
        return 0

    now = timezone.now()
    any_notice_sent = any(c.notice_sent_at is not None for c in open_cases)

    for case in open_cases:
        case.status = WalletReconciliationStatus.SETTLED
        case.settled_at = now
        case.settled_by_transaction = settling_transaction
        case.save(update_fields=['status', 'settled_at', 'settled_by_transaction', 'updated_at'])

    wallet.requires_reconciliation = False
    wallet.save(update_fields=['requires_reconciliation', 'updated_at'])

    audit(
        action='wallet.reconciliation.settled',
        entity_type='Wallet',
        entity_id=wallet.id,
        foundation_id=wallet.foundation_id,
        diff={'settled_count': len(open_cases), 'settling_transaction_id': settling_transaction.id},
    )

    # REC-020: only send a "settled" notice if a notice was actually sent for the debt.
    if any_notice_sent:
        _dispatch_reconciliation_settled_notice(wallet)

    return len(open_cases)


def _resolve_financial_guardians(student):
    """REC-007: every guardian with financial_responsible=True. Non-financial guardians never receive this."""
    from apps.identity.models import GuardianLink
    return GuardianLink.objects.filter(
        foundation_id=student.foundation_id, student=student,
        financial_responsible=True, deleted_at__isnull=True,
    ).select_related('guardian__user', 'guardian__person')


def queue_reconciliation_notice(wallet: Wallet) -> list:
    """REC-004/007/009/010/011: one combined notice per wallet per day across every
    un-notified OPEN case. Safe to call once per affected wallet after a sync batch."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    cases = list(
        WalletReconciliation.objects.filter(
            foundation_id=wallet.foundation_id, wallet=wallet,
            status=WalletReconciliationStatus.OPEN, notice_sent_at__isnull=True, deleted_at__isnull=True,
        ).order_by('detected_at')
    )
    if not cases:
        return []

    total_shortfall = sum((c.shortfall for c in cases), Decimal('0.00'))
    detected_date = timezone.localtime(cases[0].detected_at).date()
    deadline_date = detected_date + timedelta(days=7)
    dedupe_key = f"wallet_recon:{wallet.id}:{detected_date.isoformat()}"

    student = wallet.student
    guardians = list(_resolve_financial_guardians(student))

    intents = []
    for link in guardians:
        guardian = link.guardian
        if not guardian.user:
            continue
        intents.append(dispatch_intent(
            foundation_id=wallet.foundation_id,
            category=NotificationCategory.WALLET_RECONCILIATION,
            template_key='wallet.recon.notice',
            payload={
                'guardian_name': guardian.person.full_name if guardian.person else '',
                'student_name': student.person.full_name if student.person else '',
                'school_name': student.school.name if student.school else '',
                'shortfall': str(total_shortfall),
                'txn_count': str(len(cases)),
                'detected_date': detected_date.isoformat(),
                'deadline_date': deadline_date.isoformat(),
                'deep_link': 'educore://wallet',
            },
            school_id=student.school_id,
            recipient_user=guardian.user,
            recipient_phone=getattr(guardian.user, 'phone_e164', ''),
            recipient_email=getattr(guardian.user, 'email', ''),
            recipient_name=guardian.person.full_name if guardian.person else '',
            dedupe_key=dedupe_key,
        ))

    return intents


def _dispatch_reconciliation_settled_notice(wallet: Wallet):
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    student = wallet.student
    today = timezone.localdate().isoformat()
    for link in _resolve_financial_guardians(student):
        guardian = link.guardian
        if not guardian.user:
            continue
        dispatch_intent(
            foundation_id=wallet.foundation_id,
            category=NotificationCategory.WALLET_RECONCILIATION,
            template_key='wallet.recon.settled',
            payload={'student_name': student.person.full_name if student.person else ''},
            school_id=student.school_id,
            recipient_user=guardian.user,
            recipient_phone=getattr(guardian.user, 'phone_e164', ''),
            recipient_name=guardian.person.full_name if guardian.person else '',
            dedupe_key=f"wallet_recon_settled:{wallet.id}:{today}",
        )


def is_reconciliation_notice_still_needed(intent) -> bool:
    """REC-008: called from notifications.process_intent at send time. Parses the
    dedupe_key this module minted (wallet_recon:{wallet_id}:{date}) to find the wallet."""
    if not intent.dedupe_key or not intent.dedupe_key.startswith('wallet_recon:'):
        return True
    try:
        wallet_id = int(intent.dedupe_key.split(':')[1])
    except (IndexError, ValueError):
        return True
    return WalletReconciliation.objects.filter(
        foundation_id=intent.foundation_id, wallet_id=wallet_id,
        status=WalletReconciliationStatus.OPEN, deleted_at__isnull=True,
    ).exists()


def mark_reconciliation_notice_sent(intent):
    """REC-012/029: stamps notice_sent_at on the OPEN cases this intent covered, so the
    48h reminder clock starts only once a notice was actually delivered."""
    if not intent.dedupe_key or not intent.dedupe_key.startswith('wallet_recon:'):
        return
    try:
        wallet_id = int(intent.dedupe_key.split(':')[1])
    except (IndexError, ValueError):
        return
    WalletReconciliation.objects.filter(
        foundation_id=intent.foundation_id, wallet_id=wallet_id,
        status=WalletReconciliationStatus.OPEN, notice_sent_at__isnull=True, deleted_at__isnull=True,
    ).update(notice_sent_at=timezone.now())

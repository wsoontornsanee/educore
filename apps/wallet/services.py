from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.services import audit
from apps.wallet.models import (
    SpendRule,
    Wallet,
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
) -> WalletTransaction:
    """WAL-001/003/004: atomic balance update + idempotent ledger insert."""
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
    if new_balance < Decimal('0.00'):
        raise InsufficientBalanceError("INSUFFICIENT_BALANCE: transaction would take the wallet negative.")

    locked_wallet.balance = new_balance
    locked_wallet.save(update_fields=['balance', 'updated_at'])

    record = WalletTransaction.objects.create(
        foundation_id=wallet.foundation_id,
        wallet=locked_wallet,
        type=tx_type,
        amount=amount,
        balance_after=new_balance,
        reference=reference,
        occurred_at=occurred_at or timezone.now(),
        status=WalletTransactionStatus.COMPLETED,
        idempotency_key=idempotency_key,
    )
    audit(
        action='wallet.transaction.recorded',
        entity_type='WalletTransaction',
        entity_id=record.id,
        foundation_id=wallet.foundation_id,
        diff={'type': tx_type, 'amount': str(amount), 'balance_after': str(new_balance)},
    )
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
            status=WalletTransactionStatus.COMPLETED,
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
            wallet=wallet, status=WalletTransactionStatus.COMPLETED, deleted_at__isnull=True,
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

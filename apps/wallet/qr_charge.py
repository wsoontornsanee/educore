"""Canteen QR Charge: student-scanned, student-entered amount (spec 18, QRS-*).

The terminal mints a one-time signed QR (``create_qr_session``); the student's
app resolves it and submits an amount (``resolve_qr_session`` /
``charge_qr_session``). The wallet is debited server-side against a fresh,
locked balance; there is no offline path and no line items.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.wallet.models import (
    POSEntryMode,
    POSQRSession,
    POSTerminalStatus,
    POSTransaction,
    POSTransactionStatus,
    SpendRule,
    WalletStatus,
    WalletTransactionType,
)
from apps.wallet.services import (
    InsufficientBalanceError,
    _get_locked_wallet,
    check_spend_allowed,
    get_or_create_wallet,
    record_wallet_transaction,
)

QR_SESSION_TTL_SECONDS = 120  # QRS-005 default `qr_session_ttl_seconds`
_TOKEN_SALT = 'wallet.qr.session'
# No 0/O/1/I so an operator can match the code by eye across a counter.
_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

# Refusals that name a real, identified student and are therefore logged as a
# REJECTED sale (WAL-013 / QRS-018), matching card-tap POS. Token, tenant and
# switch failures never reach that point.
_LOGGED_REJECTIONS = frozenset({
    'DAILY_LIMIT_EXCEEDED', 'OUTSIDE_ALLOWED_WINDOW', 'INSUFFICIENT_BALANCE', 'AMOUNT_ABOVE_CAP',
})


class QRChargeError(ValueError):
    """A refusal carrying a stable spec-18 error ``code`` and student-facing text."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def _refuse(code: str) -> QRChargeError:
    return QRChargeError(code, _REFUSAL_TEXT[code]())


# Final copy (spec 18 design §06). Callables so translation happens at raise time.
_REFUSAL_TEXT = {
    'QR_TOKEN_INVALID': lambda: _("Kode tidak dikenali. Minta kasir menampilkan kode baru."),
    'QR_TOKEN_EXPIRED': lambda: _("Kode ini sudah kedaluwarsa. Minta kasir menampilkan kode baru."),
    'QR_TOKEN_USED': lambda: _("Kode ini sudah dipakai. Minta kasir menampilkan kode baru."),
    'MERCHANT_FOREIGN_TENANT': lambda: _("Kode ini bukan dari sekolahmu."),
    'QR_MODE_DISABLED_BY_MERCHANT': lambda: _("Bayar QR tidak tersedia di kantin ini. Tap kartumu."),
    'QR_MODE_DISABLED_BY_GUARDIAN': lambda: _("Bayar QR tidak tersedia. Tap kartumu."),
    'QR_MODE_REQUIRES_ITEMISED': lambda: _(
        "Bayar QR dimatikan karena wali kamu memblokir beberapa item. Tap kartumu."
    ),
    'AMOUNT_INVALID': lambda: _("Jumlah harus lebih dari nol."),
    'WALLET_FROZEN': lambda: _("Dompetmu sedang dibekukan. Hubungi wali atau sekolah."),
    'CURRENCY_MISMATCH': lambda: _("Mata uang dompet tidak sesuai dengan kantin ini."),
    'DAILY_LIMIT_EXCEEDED': lambda: _("Batas harian tercapai. Minta wali menaikkannya di aplikasi orang tua."),
    'OUTSIDE_ALLOWED_WINDOW': lambda: _("Di luar jam belanja yang diizinkan wali."),
    'INSUFFICIENT_BALANCE': lambda: _("Saldo tidak cukup."),
}


def _format_cap(cap: Decimal, currency: str) -> str:
    grouped = f"{int(cap):,}".replace(',', '.')
    return f"Rp {grouped}" if currency == 'IDR' else f"{currency} {cap:,.2f}"


def _above_cap_error(cap: Decimal, currency: str) -> QRChargeError:
    return QRChargeError(
        'AMOUNT_ABOVE_CAP',
        _("%(cap)s adalah batas maksimal pembayaran lewat QR.") % {'cap': _format_cap(cap, currency)},
    )


def _quantize(amount) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def set_merchant_qr_charge(merchant, enabled: bool, acknowledged: bool, actor) -> None:
    """QRS-001: enabling needs the admin's acknowledgement, recorded with name and time."""
    if enabled and not acknowledged:
        raise QRChargeError(
            'ACKNOWLEDGEMENT_REQUIRED',
            _("Konfirmasi bahwa siswa mengetik jumlah sendiri dan petugas wajib memeriksanya."),
        )
    merchant.qr_self_amount_enabled = enabled
    if enabled:
        merchant.qr_self_amount_ack_by = actor
        merchant.qr_self_amount_ack_at = timezone.now()
    merchant.save(update_fields=['qr_self_amount_enabled', 'qr_self_amount_ack_by', 'qr_self_amount_ack_at', 'updated_at'])
    audit(
        action='wallet.merchant.qr_charge_set',
        entity_type='Merchant',
        entity_id=merchant.id,
        foundation_id=merchant.foundation_id,
        diff={'enabled': enabled},
    )


def create_qr_session(terminal) -> Dict[str, Any]:
    """QRS-005: mint a signed, single-use QR for ``terminal``. Encodes no amount or student (QRS-006)."""
    merchant = terminal.merchant
    if terminal.status != POSTerminalStatus.ACTIVE or not merchant.is_active or not merchant.qr_self_amount_enabled:
        raise _refuse('QR_MODE_DISABLED_BY_MERCHANT')

    session = POSQRSession.objects.create(
        foundation_id=terminal.foundation_id, merchant=merchant, terminal=terminal,
        nonce=secrets.token_hex(8), expires_at=timezone.now() + timedelta(seconds=QR_SESSION_TTL_SECONDS),
    )
    token = signing.dumps({'s': session.id, 'n': session.nonce}, salt=_TOKEN_SALT, compress=False)
    return {'session': session, 'token': token}


def cancel_qr_session(session: POSQRSession) -> POSQRSession:
    """Regenerate/cancel (spec 18 §9): expire the session now so its QR stops resolving."""
    if session.consumed_at is None and session.expires_at > timezone.now():
        session.expires_at = timezone.now()
        session.save(update_fields=['expires_at', 'updated_at'])
    return session


def _load_session(token: str, student, lock: bool = False) -> POSQRSession:
    """Resolve ``token`` to its session and fail closed, in order: signature, tenant/school, use, expiry.

    Tenant/school is checked before use/expiry so a foreign scan learns nothing (QRS-009).
    """
    try:
        payload = signing.loads(token, salt=_TOKEN_SALT)
        session_id, nonce = int(payload['s']), str(payload['n'])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise _refuse('QR_TOKEN_INVALID')

    # all_tenants: the scanning student's tenant is authoritative, not the ambient context.
    qs = POSQRSession.all_tenants.select_related('merchant', 'merchant__school', 'terminal')
    if lock:
        qs = qs.select_for_update()
    session = qs.filter(id=session_id, deleted_at__isnull=True).first()
    if session is None or not hmac.compare_digest(session.nonce, nonce):
        raise _refuse('QR_TOKEN_INVALID')
    if session.foundation_id != student.foundation_id or session.merchant.school_id != student.school_id:
        raise _refuse('MERCHANT_FOREIGN_TENANT')
    if session.consumed_at is not None:
        raise _refuse('QR_TOKEN_USED')
    if session.expires_at <= timezone.now():
        raise _refuse('QR_TOKEN_EXPIRED')
    return session


def _check_mode_allowed(student, merchant) -> None:
    """QRS-001/002/017: merchant and guardian switches, and the itemised-blocks refusal."""
    if not merchant.is_active or not merchant.qr_self_amount_enabled:
        raise _refuse('QR_MODE_DISABLED_BY_MERCHANT')
    rule = SpendRule.objects.filter(foundation_id=student.foundation_id, student=student).first()
    if rule is None:
        return
    if not rule.qr_charge_enabled:
        raise _refuse('QR_MODE_DISABLED_BY_GUARDIAN')
    if rule.blocked_categories or rule.blocked_products:
        raise _refuse('QR_MODE_REQUIRES_ITEMISED')


def resolve_qr_session(token: str, student) -> Dict[str, Any]:
    """QRS-011: what the app shows before the keypad — stall, own balance, and the cap."""
    session = _load_session(token, student)
    merchant = session.merchant
    _check_mode_allowed(student, merchant)
    wallet = get_or_create_wallet(student)
    if wallet.status != WalletStatus.ACTIVE:
        raise _refuse('WALLET_FROZEN')
    return {
        'session_id': session.id,
        'merchant_name': merchant.name,
        'terminal_name': session.terminal.name or session.terminal.device_id,
        'balance': wallet.balance,
        'currency': wallet.currency,
        'max_amount': merchant.school.qr_self_amount_max,
        'expires_at': session.expires_at,
    }


def derive_confirmation_code(pos_transaction_id: int, nonce: str) -> str:
    """QRS-015: 4-char code from the transaction id and session nonce, identical on both screens."""
    digest = hmac.new(
        settings.SECRET_KEY.encode(), f"{pos_transaction_id}:{nonce}".encode(), hashlib.sha256,
    ).digest()
    return ''.join(_CODE_ALPHABET[b & 0x1F] for b in digest[:4])


def _existing_charge(student, client_transaction_id: str) -> Optional[POSTransaction]:
    return POSTransaction.objects.filter(
        foundation_id=student.foundation_id, student=student, entry_mode=POSEntryMode.SELF_ENTERED,
        client_transaction_id=client_transaction_id,
        status__in=[POSTransactionStatus.COMPLETED, POSTransactionStatus.VOIDED],
    ).first()


def _log_rejection(session: POSQRSession, student, amount: Decimal, client_transaction_id: str) -> None:
    POSTransaction.objects.create(
        foundation_id=session.foundation_id, merchant=session.merchant, terminal=session.terminal, student=student,
        items=[], subtotal=amount, commission=Decimal('0.00'), total=amount, occurred_at=timezone.now(),
        status=POSTransactionStatus.REJECTED, entry_mode=POSEntryMode.SELF_ENTERED, qr_session=session,
        # REJECTED rows must not occupy the idempotency slot: the student may retry the same key.
        client_transaction_id=f"{client_transaction_id}:rej:{secrets.token_hex(4)}"[:128],
    )


def charge_qr_session(token: str, student, amount, idempotency_key: str) -> POSTransaction:
    """QRS-012/016..020: debit the student's own wallet for a self-entered amount.

    Idempotent on ``idempotency_key``: a retry returns the original sale and never a second debit.
    Every check runs under the session and wallet row locks, so two phones on one token yield one
    charge and the limit/balance checks see a fresh balance.
    """
    amount = _quantize(amount)
    client_transaction_id = f"qr:{student.id}:{idempotency_key}"[:128]

    existing = _existing_charge(student, client_transaction_id)
    if existing:
        return existing

    session = None
    try:
        with transaction.atomic():
            session = _load_session(token, student, lock=True)
            merchant = session.merchant
            _check_mode_allowed(student, merchant)

            if amount <= Decimal('0.00'):
                raise _refuse('AMOUNT_INVALID')
            school = merchant.school
            if amount > school.qr_self_amount_max:
                raise _above_cap_error(school.qr_self_amount_max, school.base_currency)

            wallet = _get_locked_wallet(get_or_create_wallet(student).id, student.foundation_id)
            if wallet.status != WalletStatus.ACTIVE:
                raise _refuse('WALLET_FROZEN')
            if wallet.currency != merchant.school.base_currency:
                raise _refuse('CURRENCY_MISMATCH')

            check = check_spend_allowed(wallet, amount)
            if not check['allowed']:
                raise _refuse(check['reason'])

            occurred_at = timezone.now()
            try:
                # allow_negative stays False: QRS-020 ignores allow_overdraft, QRS-024 creates no reconciliation.
                wallet_tx = record_wallet_transaction(
                    wallet, WalletTransactionType.PURCHASE, -amount, client_transaction_id,
                    reference=f"POS:{merchant.name}", occurred_at=occurred_at,
                )
            except InsufficientBalanceError:
                raise _refuse('INSUFFICIENT_BALANCE')

            commission = (amount * merchant.commission_bps / Decimal('10000')).quantize(Decimal('0.01'))
            pos_tx = POSTransaction.objects.create(
                foundation_id=session.foundation_id, merchant=merchant, terminal=session.terminal, student=student,
                items=[], subtotal=amount, commission=commission, total=amount, occurred_at=occurred_at,
                status=POSTransactionStatus.COMPLETED, client_transaction_id=client_transaction_id,
                wallet_transaction=wallet_tx, entry_mode=POSEntryMode.SELF_ENTERED, qr_session=session,
            )
            pos_tx.confirmation_code = derive_confirmation_code(pos_tx.id, session.nonce)
            pos_tx.save(update_fields=['confirmation_code', 'updated_at'])

            session.consumed_at = occurred_at
            session.consumed_by_student = student
            session.save(update_fields=['consumed_at', 'consumed_by_student', 'updated_at'])

            audit(
                action='wallet.pos_transaction.completed',
                entity_type='POSTransaction',
                entity_id=pos_tx.id,
                foundation_id=session.foundation_id,
                diff={'merchant': merchant.name, 'total': str(amount), 'entry_mode': POSEntryMode.SELF_ENTERED},
            )
            return pos_tx
    except QRChargeError as exc:
        if session is not None and exc.code in _LOGGED_REJECTIONS:
            _log_rejection(session, student, amount, client_transaction_id)
        raise


def get_qr_session_result(session: POSQRSession) -> Dict[str, Any]:
    """Terminal poll during a live session (spec 18 §9): PENDING, PAID (with the sale) or EXPIRED."""
    if session.consumed_at is not None:
        pos_tx = POSTransaction.objects.filter(
            foundation_id=session.foundation_id, qr_session=session, status__in=[
                POSTransactionStatus.COMPLETED, POSTransactionStatus.VOIDED,
            ],
        ).select_related('student', 'student__person').first()
        return {'status': 'PAID', 'transaction': pos_tx}
    if session.expires_at <= timezone.now():
        return {'status': 'EXPIRED', 'transaction': None}
    return {'status': 'PENDING', 'transaction': None}

"""Canteen QR Charge: student-scanned, student-entered amount (spec 18, QRS-*).

The terminal mints a one-time signed QR (``create_qr_session``); the student's
app resolves it and submits an amount (``resolve_qr_session`` /
``charge_qr_session``). The wallet is debited server-side against a fresh,
locked balance; there is no offline path and no line items.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass
from typing import Any, Dict, Optional

import segno
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.wallet.models import (
    POSEntryMode,
    POSPaymentPointStatus,
    POSQRDecal,
    POSQRDecalStatus,
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

logger = logging.getLogger(__name__)

QR_SESSION_TTL_SECONDS = 120  # QRS-005 default `qr_session_ttl_seconds`
_TOKEN_SALT = 'wallet.qr.session'
DECAL_TOKEN_SALT = 'wallet.qr.decal'
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
    'QR_DECAL_REVOKED': lambda: _("Lembar ini sudah tidak berlaku. Minta petugas lembar yang terbaru."),
    'QR_DECAL_EXPIRED': lambda: _("Lembar ini sudah kedaluwarsa. Minta petugas lembar yang terbaru."),
    'PAYMENT_POINT_CLOSED': lambda: _("Titik pembayaran ini sedang ditutup."),
    'AMOUNT_ABOVE_CAP': lambda: _("Melebihi batas pembayaran lewat QR."),
    'AMOUNT_INVALID': lambda: _("Jumlah harus lebih dari nol."),
    'WALLET_FROZEN': lambda: _("Dompetmu sedang dibekukan. Hubungi wali atau sekolah."),
    'CURRENCY_MISMATCH': lambda: _("Mata uang dompet tidak sesuai dengan kantin ini."),
    'DAILY_LIMIT_EXCEEDED': lambda: _("Batas harian tercapai. Minta wali menaikkannya di aplikasi orang tua."),
    'OUTSIDE_ALLOWED_WINDOW': lambda: _("Di luar jam belanja yang diizinkan wali."),
    'INSUFFICIENT_BALANCE': lambda: _("Saldo tidak cukup."),
}


def refusal_message(code: str) -> str:
    """Student-facing text for a spec-18 refusal code (the code itself if it has none)."""
    text = _REFUSAL_TEXT.get(code)
    return text() if text else code


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


def render_qr_svg(token: str) -> str:
    """Inline SVG of ``token`` for the terminal screen (QRS-007): black on white, 4-module quiet zone.

    Rendered server-side so the page needs no third-party QR script and the token never
    leaves our own response.
    """
    return segno.make(token, error='m').svg_inline(scale=8, border=4, dark='#000', light='#fff', omitsize=True)


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


def effective_cap(merchant, static: bool = False) -> Decimal:
    """QRS-004/008: the school cap, and for a printed decal the lower of it and ``static_qr_max``."""
    cap = merchant.school.qr_self_amount_max
    return min(cap, merchant.static_qr_max) if static else cap


@dataclass
class _Target:
    """What a scanned token points at: a terminal session (single use) or a printed decal (reusable)."""
    session: Optional[POSQRSession] = None
    decal: Optional[POSQRDecal] = None

    @property
    def is_static(self) -> bool:
        return self.decal is not None

    @property
    def merchant(self):
        return self.decal.payment_point.merchant if self.decal else self.session.merchant

    @property
    def terminal(self):
        return None if self.decal else self.session.terminal

    @property
    def nonce(self) -> str:
        return (self.decal or self.session).nonce

    @property
    def foundation_id(self) -> int:
        return (self.decal or self.session).foundation_id

    @property
    def label(self) -> str:
        if self.decal:
            return self.decal.payment_point.name
        return self.session.terminal.name or self.session.terminal.device_id


def _parse_token(token: str):
    for kind, salt, key in (('SESSION', _TOKEN_SALT, 's'), ('DECAL', DECAL_TOKEN_SALT, 'd')):
        try:
            payload = signing.loads(token, salt=salt)
            return kind, int(payload[key]), str(payload['n'])
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            continue
    raise _refuse('QR_TOKEN_INVALID')


def _load_session(session_id: int, nonce: str, student, lock: bool) -> POSQRSession:
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


def _load_decal(decal_id: int, nonce: str, student) -> POSQRDecal:
    decal = POSQRDecal.all_tenants.select_related(
        'payment_point', 'payment_point__merchant', 'payment_point__merchant__school',
    ).filter(id=decal_id, deleted_at__isnull=True).first()
    if decal is None or not hmac.compare_digest(decal.nonce, nonce):
        raise _refuse('QR_TOKEN_INVALID')
    merchant = decal.payment_point.merchant
    if decal.foundation_id != student.foundation_id or merchant.school_id != student.school_id:
        raise _refuse('MERCHANT_FOREIGN_TENANT')
    now = timezone.now()
    if decal.status == POSQRDecalStatus.REVOKED or (
        decal.status == POSQRDecalStatus.SUPERSEDED and (decal.grace_until is None or decal.grace_until <= now)
    ):
        raise _refuse('QR_DECAL_REVOKED')
    if decal.expires_on is not None and decal.expires_on < timezone.localdate():
        raise _refuse('QR_DECAL_EXPIRED')
    if decal.payment_point.status != POSPaymentPointStatus.ACTIVE:
        raise _refuse('PAYMENT_POINT_CLOSED')
    return decal


def _load_target(token: str, student, lock: bool = False) -> _Target:
    """Resolve ``token`` and fail closed, in order: signature, tenant/school, then state.

    Tenant/school is checked before use/expiry so a foreign scan learns nothing (QRS-009).
    """
    kind, target_id, nonce = _parse_token(token)
    if kind == 'SESSION':
        return _Target(session=_load_session(target_id, nonce, student, lock))
    return _Target(decal=_load_decal(target_id, nonce, student))


def _check_mode_allowed(student, merchant, static: bool = False) -> None:
    """QRS-001/002/008/017: merchant and guardian switches, and the itemised-blocks refusal."""
    if not merchant.is_active or not merchant.qr_self_amount_enabled or (static and not merchant.static_qr_enabled):
        raise _refuse('QR_MODE_DISABLED_BY_MERCHANT')
    rule = SpendRule.objects.filter(foundation_id=student.foundation_id, student=student).first()
    if rule is None:
        return
    if not rule.qr_charge_enabled:
        raise _refuse('QR_MODE_DISABLED_BY_GUARDIAN')
    if rule.blocked_categories or rule.blocked_products:
        raise _refuse('QR_MODE_REQUIRES_ITEMISED')


def resolve_qr_session(token: str, student) -> Dict[str, Any]:
    """QRS-011/037: what the app shows before the keypad — stall or counter, own balance, and the cap."""
    target = _load_target(token, student)
    merchant = target.merchant
    _check_mode_allowed(student, merchant, static=target.is_static)
    wallet = get_or_create_wallet(student)
    if wallet.status != WalletStatus.ACTIVE:
        raise _refuse('WALLET_FROZEN')
    return {
        'type': 'STATIC' if target.is_static else 'SESSION',
        'session_id': target.session.id if target.session else None,
        'merchant_name': merchant.name,
        'terminal_name': target.label,
        'payment_point_name': target.decal.payment_point.name if target.decal else None,
        'payment_point_location': target.decal.payment_point.location if target.decal else None,
        'balance': wallet.balance,
        'currency': wallet.currency,
        'max_amount': effective_cap(merchant, target.is_static),
        'expires_at': target.session.expires_at if target.session else None,
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


def _log_rejection(target: _Target, student, amount: Decimal, client_transaction_id: str, reason: str) -> None:
    POSTransaction.objects.create(
        foundation_id=target.foundation_id, merchant=target.merchant, terminal=target.terminal, student=student,
        items=[], subtotal=amount, commission=Decimal('0.00'), total=amount, occurred_at=timezone.now(),
        status=POSTransactionStatus.REJECTED, entry_mode=POSEntryMode.SELF_ENTERED, reject_reason=reason,
        qr_session=target.session, qr_decal=target.decal,
        # REJECTED rows must not occupy the idempotency slot: the student may retry the same key.
        client_transaction_id=f"{client_transaction_id}:rej:{secrets.token_hex(4)}"[:128],
    )


def charge_qr_session(token: str, student, amount, idempotency_key: str) -> POSTransaction:
    """QRS-012/016..020: debit the student's own wallet for a self-entered amount.

    Idempotent on ``idempotency_key``: a retry returns the original sale and never a second debit.
    Every check runs under the session and wallet row locks, so two phones on one terminal token yield
    one charge and the limit/balance checks see a fresh balance. A static decal is reusable, so it is
    never consumed; the wallet row lock and idempotency key alone serialise a student's charges.
    """
    amount = _quantize(amount)
    client_transaction_id = f"qr:{student.id}:{idempotency_key}"[:128]

    existing = _existing_charge(student, client_transaction_id)
    if existing:
        return existing

    target = None
    try:
        with transaction.atomic():
            target = _load_target(token, student, lock=True)
            merchant = target.merchant
            _check_mode_allowed(student, merchant, static=target.is_static)

            if amount <= Decimal('0.00'):
                raise _refuse('AMOUNT_INVALID')
            cap = effective_cap(merchant, target.is_static)
            school = merchant.school
            if amount > cap:
                raise _above_cap_error(cap, school.base_currency)

            wallet = _get_locked_wallet(get_or_create_wallet(student).id, student.foundation_id)
            if wallet.status != WalletStatus.ACTIVE:
                raise _refuse('WALLET_FROZEN')
            if wallet.currency != school.base_currency:
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
                foundation_id=target.foundation_id, merchant=merchant, terminal=target.terminal, student=student,
                items=[], subtotal=amount, commission=commission, total=amount, occurred_at=occurred_at,
                status=POSTransactionStatus.COMPLETED, client_transaction_id=client_transaction_id,
                wallet_transaction=wallet_tx, entry_mode=POSEntryMode.SELF_ENTERED,
                qr_session=target.session, qr_decal=target.decal,
            )
            pos_tx.confirmation_code = derive_confirmation_code(pos_tx.id, target.nonce)
            pos_tx.save(update_fields=['confirmation_code', 'updated_at'])

            if target.session:
                target.session.consumed_at = occurred_at
                target.session.consumed_by_student = student
                target.session.save(update_fields=['consumed_at', 'consumed_by_student', 'updated_at'])

            audit(
                action='wallet.pos_transaction.completed',
                entity_type='POSTransaction',
                entity_id=pos_tx.id,
                foundation_id=target.foundation_id,
                diff={
                    'merchant': merchant.name, 'total': str(amount), 'entry_mode': POSEntryMode.SELF_ENTERED,
                    'static': target.is_static,
                },
            )
    except QRChargeError as exc:
        if target is not None and exc.code in _LOGGED_REJECTIONS:
            _log_rejection(target, student, amount, client_transaction_id, exc.code)
        raise

    if target.is_static:
        _alert_on_static_charge(pos_tx)
    return pos_tx


def _alert_on_static_charge(pos_tx: POSTransaction) -> None:
    """QRS-041, after commit. An alerting fault must never fail or undo a payment that already succeeded."""
    try:
        from apps.wallet.qr_alerts import check_static_charge_anomalies
        check_static_charge_anomalies(pos_tx)
    except Exception:
        logger.exception("QRS-041 decal anomaly check failed for POS transaction %s", pos_tx.id)


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

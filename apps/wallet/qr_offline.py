"""Canteen QR Charge: offline-terminal-minted session tokens (spec 18 §6, QRS-022/023).

An offline terminal cannot ask the server to mint a QR session (that's
``qr_charge.create_qr_session``, which needs a live round-trip). Instead the
terminal signs its own SESSION token locally with a per-terminal secret
(``POSTerminalSessionKey``), the student scans it, and the sale is captured
locally exactly like any other offline POS sale (``services.process_offline_
pos_batch``). Only at sync time — when the terminal is back online — does the
server verify the signature, the terminal binding, and that the token's
nonce hasn't been used before (WAL-015: no duplicates on reconciliation).

Token shape mirrors the online session's "no amount/student encoded"
invariant (QRS-005/006): the token only proves "a real, paired terminal
minted this single-use session at this moment" — never the sale itself.

Two consumers verify the same token: the terminal's offline sync
(``process_offline_pos_batch``) and the student's own online charge
(``qr_charge.charge_qr_session``, QRS-022), which is what actually debits the
wallet. Whichever runs first claims the nonce; the other only reconciles.

Wire format (what the RN terminal client must produce):
    token = "<payload_b64>.<hmac_hex>"
    payload = {"terminal_device_id", "key_id", "nonce", "minted_at", "expires_at"}
    hmac_hex = HMAC-SHA256(secret, payload_b64).hexdigest()
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.wallet.crypto import decrypt_secret, encrypt_secret
from apps.wallet.models import POSTerminalSessionKey, POSTerminalSessionKeyStatus

CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


class OfflineTokenError(ValidationError):
    """An offline-minted QR token failed verification; ``code`` is a stable
    machine-readable reason (mirrors QRChargeError's shape)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def generate_key_id() -> str:
    return f"potk_{secrets.token_urlsafe(9)}"


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def issue_terminal_session_key(terminal, actor_id='', ip_address=None) -> tuple[POSTerminalSessionKey, str]:
    """Issue a new offline-signing key for `terminal`. Returns
    (key, plaintext_secret) — the ONLY moment the secret is ever visible.
    Any existing ACTIVE key for this terminal is revoked with a grace period
    (GRACE_PERIOD_DAYS) so offline sales it already signed still sync."""
    secret = generate_secret()
    key = POSTerminalSessionKey.objects.create(
        foundation_id=terminal.foundation_id, terminal=terminal,
        key_id=generate_key_id(), secret_encrypted=encrypt_secret(secret),
    )
    _revoke_other_active_keys(terminal, keep=key)
    audit(
        action='wallet.pos_terminal_session_key.issued', entity_type='POSTerminalSessionKey',
        entity_id=str(key.id), actor_id=actor_id, foundation_id=terminal.foundation_id,
        ip_address=ip_address, diff={'terminal_id': terminal.id, 'key_id': key.key_id},
    )
    return key, secret


def revoke_terminal_session_key(key: POSTerminalSessionKey, actor_id='', ip_address=None) -> None:
    if key.status == POSTerminalSessionKeyStatus.REVOKED:
        return
    key.status = POSTerminalSessionKeyStatus.REVOKED
    key.revoked_at = timezone.now()
    key.grace_until = key.revoked_at + timedelta(days=POSTerminalSessionKey.GRACE_PERIOD_DAYS)
    key.save(update_fields=['status', 'revoked_at', 'grace_until', 'updated_at'])
    audit(
        action='wallet.pos_terminal_session_key.revoked', entity_type='POSTerminalSessionKey',
        entity_id=str(key.id), actor_id=actor_id, foundation_id=key.foundation_id,
        ip_address=ip_address, diff={'terminal_id': key.terminal_id, 'key_id': key.key_id},
    )


def _revoke_other_active_keys(terminal, keep) -> None:
    others = POSTerminalSessionKey.objects.filter(
        foundation_id=terminal.foundation_id, terminal=terminal,
        status=POSTerminalSessionKeyStatus.ACTIVE,
    ).exclude(id=keep.id)
    now = timezone.now()
    for other in others:
        other.status = POSTerminalSessionKeyStatus.REVOKED
        other.revoked_at = now
        other.grace_until = now + timedelta(days=POSTerminalSessionKey.GRACE_PERIOD_DAYS)
        other.save(update_fields=['status', 'revoked_at', 'grace_until', 'updated_at'])


def mint_offline_session_token(key: POSTerminalSessionKey, secret: str, ttl_seconds: int = 120) -> str:
    """Reference implementation of what the terminal does locally, offline,
    with no server round-trip. Used by tests; the real signer lives in the
    RN terminal client, which must produce byte-identical payload encoding."""
    now = timezone.now()
    payload = {
        'terminal_device_id': key.terminal.device_id,
        'key_id': key.key_id,
        'nonce': secrets.token_hex(16),
        'minted_at': now.isoformat(),
        'expires_at': (now + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    return _sign(payload, secret)


def _sign(payload: dict, secret: str) -> str:
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    ).decode().rstrip('=')
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _decode(token: str) -> tuple:
    """Split ``token`` into (payload_b64, sig_hex, payload). Malformed input raises MALFORMED."""
    try:
        payload_b64, sig_hex = token.rsplit('.', 1)
        padded = payload_b64 + '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        raise OfflineTokenError('MALFORMED', _("Token QR tidak valid."))
    return payload_b64, sig_hex, payload


def peek_key_id(token: str) -> str:
    """The unverified ``key_id`` a token claims, so a caller can locate the key (and its terminal).
    Nothing read here is trusted until ``verify_offline_session_token`` passes."""
    return str(_decode(token)[2].get('key_id') or '')


def verify_offline_session_token(terminal, token: str, occurred_at) -> tuple:
    """Verify a terminal-minted offline token against `terminal` and `occurred_at`:
    the terminal's own clock at scan time when syncing (NOT wall-clock "now" —
    sync can legitimately happen days later), or server "now" on the student's
    online charge. Returns (key, nonce) on success, so the caller can record
    which key authenticated the sale and enforce single-use (WAL-015).
    Fail-closed order mirrors qr_charge._load_session."""
    payload_b64, sig_hex, payload = _decode(token)

    key = POSTerminalSessionKey.all_tenants.filter(
        foundation_id=terminal.foundation_id, terminal=terminal, key_id=payload.get('key_id'),
        deleted_at__isnull=True,
    ).first()
    if key is None or not key.is_verifiable:
        raise OfflineTokenError('KEY_INVALID', _("Kunci terminal tidak valid atau sudah dicabut."))

    expected_sig = hmac.new(decrypt_secret(key.secret_encrypted).encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig_hex):
        raise OfflineTokenError('BAD_SIGNATURE', _("Tanda tangan token QR tidak cocok."))

    if payload.get('terminal_device_id') != terminal.device_id:
        raise OfflineTokenError('TERMINAL_MISMATCH', _("Token QR ini bukan milik terminal ini."))

    try:
        minted_at = datetime.fromisoformat(payload['minted_at'])
        expires_at = datetime.fromisoformat(payload['expires_at'])
    except (KeyError, ValueError, TypeError):
        raise OfflineTokenError('MALFORMED', _("Token QR tidak valid."))
    if expires_at < minted_at:
        raise OfflineTokenError('MALFORMED', _("Token QR tidak valid."))
    if not (minted_at - CLOCK_SKEW_TOLERANCE <= occurred_at <= expires_at + CLOCK_SKEW_TOLERANCE):
        raise OfflineTokenError('EXPIRED', _("Token QR sudah kedaluwarsa."))

    nonce = payload.get('nonce')
    if not nonce:
        raise OfflineTokenError('MALFORMED', _("Token QR tidak valid."))
    return key, str(nonce)

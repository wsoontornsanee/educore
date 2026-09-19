"""Guardian spending PIN (spec 18 QRS-029): storage, strength rules and attempt policy.

Design (OWASP Password Storage cheat sheet, NIST SP 800-63B-4):
- A 6-digit PIN has 10^6 values, so an offline attack on a leaked hash is trivial unless the verifier
  also depends on a secret. Storage is Argon2id (OWASP minimum m=19 MiB, t=2, p=1) whose raw digest is
  then HMAC-SHA256'd with a server-side pepper that lives outside the database.
- The real control is the online attempt limit, enforced server-side per user: 3 consecutive failures
  lock the PIN for 15 minutes; 10 within 24 h require an OTP re-verification to set a new PIN
  (mirrors IAM-008's "unlock by OTP"). NIST allows up to 100 and permits stricter limits.
"""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from argon2.exceptions import HashingError
from argon2.low_level import Type, hash_secret_raw
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.services import audit

from .models import UserPin

PIN_LENGTH = 6
LOCK_AFTER_FAILURES = 3
LOCK_DURATION = timedelta(minutes=15)
OTP_RESET_AFTER_FAILURES = 10
FAILURE_WINDOW = timedelta(hours=24)

# OWASP minimum Argon2id configuration; parameters travel with the verifier so they can be raised later.
_M_COST, _T_COST, _PARALLELISM, _HASH_LEN = 19456, 2, 1, 32
_SCHEME = 'argon2id-hmac'


class PinError(ValueError):
    """A PIN refusal with a stable ``code`` and student/guardian-facing ``message``."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(code)
        self.code = code
        self.message = message
        self.extra = extra


def _pepper() -> bytes:
    raw = getattr(settings, 'EDUCORE_PIN_PEPPER', '') or ''
    if raw:
        return raw.encode()
    return hashlib.sha256(b'educore-pin-pepper:' + str(settings.SECRET_KEY).encode()).digest()


def _digest(pin: str, salt: bytes, m: int, t: int, p: int) -> bytes:
    raw = hash_secret_raw(
        secret=pin.encode(), salt=salt, time_cost=t, memory_cost=m, parallelism=p, hash_len=_HASH_LEN, type=Type.ID,
    )
    return hmac.new(_pepper(), raw, hashlib.sha256).digest()


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _digest(pin, salt, _M_COST, _T_COST, _PARALLELISM)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"{_SCHEME}${_M_COST},{_T_COST},{_PARALLELISM}${b64(salt)}${b64(digest)}"


def verify_pin_hash(pin: str, stored: str) -> bool:
    try:
        scheme, params, salt_b64, digest_b64 = stored.split('$')
        m, t, p = (int(x) for x in params.split(','))
        if scheme != _SCHEME:
            return False
        expected = base64.b64decode(digest_b64)
        actual = _digest(pin, base64.b64decode(salt_b64), m, t, p)
    except (ValueError, TypeError, HashingError):
        return False
    return hmac.compare_digest(expected, actual)


def _is_sequential(pin: str) -> bool:
    digits = [int(c) for c in pin]
    steps = {b - a for a, b in zip(digits, digits[1:])}
    return steps in ({1}, {-1})


def validate_pin_strength(pin: str, user=None) -> None:
    """Refuse malformed and trivially guessable PINs (NIST: reject commonly used secrets)."""
    if not re.fullmatch(rf"[0-9]{{{PIN_LENGTH}}}", pin or ''):  # ASCII digits only, not Unicode \d
        raise PinError('PIN_INVALID_FORMAT', _("PIN harus terdiri dari 6 angka."))
    weak = (
        len(set(pin)) == 1                       # 111111
        or _is_sequential(pin)                   # 123456, 654321
        or pin[:3] == pin[3:]                    # 123123
        or pin[:2] * 3 == pin                    # 121212
        or pin == pin[::-1]                      # 123321
    )
    phone = getattr(user, 'phone_e164', '') or ''
    if weak or (phone and pin == phone[-PIN_LENGTH:]):
        raise PinError('PIN_TOO_WEAK', _("PIN terlalu mudah ditebak. Pilih kombinasi angka lain."))


def get_pin_status(user) -> dict:
    pin = UserPin.objects.filter(user=user, foundation_id=user.foundation_id).first()
    now = timezone.now()
    return {
        'is_set': pin is not None,
        'locked_until': pin.locked_until if pin and pin.locked_until and pin.locked_until > now else None,
        'requires_otp_reset': bool(pin and pin.requires_otp_reset),
    }


def _store(user, pin: str, action: str) -> UserPin:
    record, _created = UserPin.objects.update_or_create(
        user=user, foundation_id=user.foundation_id,
        defaults={
            'pin_hash': hash_pin(pin), 'set_at': timezone.now(), 'failed_count': 0, 'first_failed_at': None,
            'locked_until': None, 'requires_otp_reset': False,
        },
    )
    audit(action=action, entity_type='UserPin', entity_id=record.id, foundation_id=user.foundation_id, diff={})
    return record


def set_pin(user, pin: str) -> UserPin:
    """First-time setup only; changing needs the current PIN, forgetting it needs an OTP."""
    validate_pin_strength(pin, user)
    if UserPin.objects.filter(user=user, foundation_id=user.foundation_id).exists():
        raise PinError('PIN_ALREADY_SET', _("PIN sudah dibuat. Gunakan ubah PIN atau atur ulang lewat OTP."))
    return _store(user, pin, 'identity.pin.set')


def change_pin(user, current_pin: str, new_pin: str) -> UserPin:
    validate_pin_strength(new_pin, user)
    check_pin(user, current_pin)  # counts against the attempt limit like any other entry
    return _store(user, new_pin, 'identity.pin.changed')


def reset_pin_with_otp(user, challenge_id: int, code: str, new_pin: str) -> UserPin:
    """Forgotten or locked-out PIN: prove control of the account's phone with a fresh OTP (IAM-003)."""
    from .models import OTPChallenge
    from .services import verify_phone_otp

    validate_pin_strength(new_pin, user)
    challenge = OTPChallenge.objects.filter(id=challenge_id).first()
    if challenge is None or challenge.phone_e164 != user.phone_e164:
        raise PinError('OTP_INVALID', _("Kode OTP tidak valid."))
    ok, _message = verify_phone_otp(challenge_id, code)
    if not ok:
        raise PinError('OTP_INVALID', _("Kode OTP tidak valid."))
    return _store(user, new_pin, 'identity.pin.reset')


def check_pin(user, pin: str) -> None:
    """Verify a PIN entry and apply the attempt policy. Returns None on success.

    Raises ``PIN_NOT_SET``, ``PIN_LOCKED`` (with ``retry_after_seconds``), ``PIN_RESET_REQUIRED`` or
    ``PIN_INVALID`` (with ``attempts_left``). The outcome is decided inside a transaction that locks the
    row (parallel guesses cannot beat the limit) and the error is raised only after it commits — raising
    inside would roll back the very failure count that enforces the limit.
    """
    error = _evaluate_pin(user, pin)
    if error is not None:
        raise error


@transaction.atomic
def _evaluate_pin(user, pin: str):
    record = UserPin.objects.select_for_update().filter(user=user, foundation_id=user.foundation_id).first()
    if record is None:
        return PinError('PIN_NOT_SET', _("Buat PIN dulu sebelum membayar dengan QR."))
    now = timezone.now()
    if record.requires_otp_reset:
        return PinError('PIN_RESET_REQUIRED', _("PIN diblokir. Atur ulang PIN dengan kode OTP."))
    if record.locked_until and record.locked_until > now:
        return PinError(
            'PIN_LOCKED', _("PIN dikunci sementara. Coba lagi nanti."),
            retry_after_seconds=int((record.locked_until - now).total_seconds()) + 1,
        )

    if verify_pin_hash(pin or '', record.pin_hash):
        if record.failed_count or record.locked_until:
            record.failed_count, record.first_failed_at, record.locked_until = 0, None, None
            record.save(update_fields=['failed_count', 'first_failed_at', 'locked_until', 'updated_at'])
        return None

    if record.first_failed_at is None or now - record.first_failed_at > FAILURE_WINDOW:
        record.failed_count, record.first_failed_at = 0, now
    record.failed_count += 1
    error = PinError(
        'PIN_INVALID', _("PIN salah."),
        attempts_left=LOCK_AFTER_FAILURES - (record.failed_count % LOCK_AFTER_FAILURES),
    )
    if record.failed_count >= OTP_RESET_AFTER_FAILURES:
        record.requires_otp_reset = True
        audit(action='identity.pin.reset_required', entity_type='UserPin', entity_id=record.id,
              foundation_id=user.foundation_id, diff={'failures': record.failed_count})
        error = PinError('PIN_RESET_REQUIRED', _("PIN diblokir. Atur ulang PIN dengan kode OTP."))
    elif record.failed_count % LOCK_AFTER_FAILURES == 0:
        record.locked_until = now + LOCK_DURATION
        audit(action='identity.pin.locked', entity_type='UserPin', entity_id=record.id,
              foundation_id=user.foundation_id, diff={'failures': record.failed_count})
        error = PinError(
            'PIN_LOCKED', _("PIN dikunci sementara. Coba lagi nanti."),
            retry_after_seconds=int(LOCK_DURATION.total_seconds()),
        )
    record.save(update_fields=['failed_count', 'first_failed_at', 'locked_until', 'requires_otp_reset', 'updated_at'])
    return error


def pin_error_response(error: PinError):
    """DRF response for a PinError: 400 for input problems, 423 while locked or awaiting an OTP reset."""
    from rest_framework import status
    from rest_framework.response import Response

    locked = error.code in ('PIN_LOCKED', 'PIN_RESET_REQUIRED')
    return Response(
        {'error': error.code, 'message': error.message, **error.extra},
        status=status.HTTP_423_LOCKED if locked else status.HTTP_400_BAD_REQUEST,
    )

"""Campus-life note encryption at rest: clinic visits (spec/10 LIF-007) and
counselling (BK) sessions (spec/10 §5, LIF-015).

Same Fernet-per-app-key convention as `apps.hardware.crypto` /
`apps.partners.crypto` / `apps.calendar_sync.crypto`, one key per domain
within this app: EDUCORE_CLINIC_FERNET_KEY for clinic notes,
EDUCORE_COUNSELLING_FERNET_KEY for counselling notes — both falling back to
a SECRET_KEY-derived key so dev/test work out of the box; production must
set explicit keys.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet_for(setting_name: str) -> Fernet:
    raw = getattr(settings, setting_name, '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            return Fernet(key)
        except Exception as exc:
            raise RuntimeError(
                f"{setting_name} is set but is not a valid Fernet key (must be 32 "
                "url-safe base64-encoded bytes). Fix the setting — do not rely on "
                "the SECRET_KEY-derived dev fallback in production."
            ) from exc
    key = base64.urlsafe_b64encode(hashlib.sha256(str(settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def _get_fernet() -> Fernet:
    return _get_fernet_for('EDUCORE_CLINIC_FERNET_KEY')


def _get_counselling_fernet() -> Fernet:
    return _get_fernet_for('EDUCORE_COUNSELLING_FERNET_KEY')


def encrypt_note(raw_text: str) -> str:
    return _get_fernet().encrypt(raw_text.encode()).decode()


def decrypt_note(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Clinic note decryption failed: Fernet key mismatch.")


def encrypt_notes(plaintext: str) -> str:
    return _get_counselling_fernet().encrypt((plaintext or '').encode()).decode()


def decrypt_notes(ciphertext: str) -> str:
    if not ciphertext:
        return ''
    try:
        return _get_counselling_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Counselling note decryption failed: Fernet key mismatch.")
    except Exception as exc:
        # Malformed ciphertext (corrupt write, manual DB edit) raises something
        # other than InvalidToken (e.g. a base64/padding error) before Fernet
        # even reaches the HMAC check — surface the same clear error instead of
        # letting a raw decode exception 500 every list/retrieve of this row.
        raise RuntimeError("Counselling note decryption failed: malformed ciphertext.") from exc

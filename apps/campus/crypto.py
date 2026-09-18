"""Counselling (BK) session note encryption at rest (spec/10 §5, LIF-015).

Same Fernet-per-app-key convention as `apps.hardware.crypto` /
`apps.partners.crypto` / `apps.calendar_sync.crypto`: the key comes from
EDUCORE_COUNSELLING_FERNET_KEY, falling back to a SECRET_KEY-derived key so
dev/test work out of the box — production must set an explicit key.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    raw = getattr(settings, 'EDUCORE_COUNSELLING_FERNET_KEY', '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            Fernet(key)
            return Fernet(key)
        except Exception:
            key = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
            return Fernet(key)
    key = base64.urlsafe_b64encode(hashlib.sha256(str(settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def encrypt_notes(plaintext: str) -> str:
    return _get_fernet().encrypt((plaintext or '').encode()).decode()


def decrypt_notes(ciphertext: str) -> str:
    if not ciphertext:
        return ''
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Counselling note decryption failed: Fernet key mismatch.")
    except Exception as exc:
        # Malformed ciphertext (corrupt write, manual DB edit) raises something
        # other than InvalidToken (e.g. a base64/padding error) before Fernet
        # even reaches the HMAC check — surface the same clear error instead of
        # letting a raw decode exception 500 every list/retrieve of this row.
        raise RuntimeError("Counselling note decryption failed: malformed ciphertext.") from exc

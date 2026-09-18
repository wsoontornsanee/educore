"""Biometric template encryption at rest (spec/14 CMP-003, HW-010).

Same Fernet-per-app-key convention as `apps.partners.crypto` /
`apps.calendar_sync.crypto`: the key comes from EDUCORE_BIOMETRIC_FERNET_KEY,
falling back to a SECRET_KEY-derived key so dev/test work out of the box —
production must set an explicit key.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    raw = getattr(settings, 'EDUCORE_BIOMETRIC_FERNET_KEY', '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            return Fernet(key)
        except Exception as exc:
            raise RuntimeError(
                "EDUCORE_BIOMETRIC_FERNET_KEY is set but is not a valid Fernet key "
                "(must be 32 url-safe base64-encoded bytes). Fix the setting — do not "
                "rely on the SECRET_KEY-derived dev fallback in production."
            ) from exc
    key = base64.urlsafe_b64encode(hashlib.sha256(str(settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def encrypt_template(raw_template: str) -> str:
    return _get_fernet().encrypt(raw_template.encode()).decode()


def decrypt_template(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Biometric template decryption failed: Fernet key mismatch.")

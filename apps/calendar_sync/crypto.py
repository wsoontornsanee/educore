"""OAuth token encryption at rest for calendar connections (UU PDP / rule 8).

Mirrors apps/partners/crypto.py's Fernet pattern — each app owns its own key
setting so a partners-key rotation never breaks calendar tokens. The key comes
from EDUCORE_CALENDAR_FERNET_KEY; when unset a derived key from SECRET_KEY is
used so dev/test environments work out of the box — production must set an
explicit key.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    raw = getattr(settings, 'EDUCORE_CALENDAR_FERNET_KEY', '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            return Fernet(key)
        except Exception as exc:
            raise RuntimeError(
                "EDUCORE_CALENDAR_FERNET_KEY is set but is not a valid Fernet key "
                "(must be 32 url-safe base64-encoded bytes). Fix the setting — do not "
                "rely on the SECRET_KEY-derived dev fallback in production."
            ) from exc
    key = base64.urlsafe_b64encode(hashlib.sha256(str(settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ''
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Key rotated without re-encrypting — fail loud, never send garbage
        # to the provider as a bearer token.
        raise RuntimeError("Calendar token decryption failed: Fernet key mismatch.")

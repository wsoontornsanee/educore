"""Secret encryption at rest for POS terminal offline session keys (UU PDP / rule 8).

Mirrors apps/partners/crypto.py exactly (same Fernet-at-rest approach, same
dev fallback), kept as its own module so a wallet-specific key can be
rotated independently of the partner-API one.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    raw = getattr(settings, 'EDUCORE_WALLET_FERNET_KEY', '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            return Fernet(key)
        except Exception as exc:
            raise RuntimeError(
                "EDUCORE_WALLET_FERNET_KEY is set but is not a valid Fernet key "
                "(must be 32 url-safe base64-encoded bytes). Fix the setting — do not "
                "rely on the SECRET_KEY-derived dev fallback in production."
            ) from exc
    # Deterministic dev fallback derived from SECRET_KEY.
    key = base64.urlsafe_b64encode(hashlib.sha256((str(settings.SECRET_KEY) + ':wallet').encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Key rotated without re-encrypting — fail loud, never send garbage to HMAC.
        raise RuntimeError("POS terminal session key decryption failed: Fernet key mismatch.")

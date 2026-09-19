"""Canonical Indonesian PII-type registry (spec/14 §3, AGENTS Red Line #5).

Single source of truth for "what counts as PII in this system and how do we
detect/redact it," consumed by both ``apps.core.logging``'s live log
scrubbing (``PIIScrubbingFilter``/``scrub_value``) and ``apps.core.services``'
export PII registration (``register_export_pii``/``get_export_pii_types``).
A new PII type is added here once and is then covered by both live logs and
export audit trails.

Not merged with ``apps.compliance.services``' ``NIK_PATTERN``/``NISN_PATTERN``:
those are anchored full-match *validation* regexes (statutory field format
checks), a different concern from the unanchored, boundary-delimited
*detection-in-free-text* regexes here used for redaction.
"""
import re
from enum import Enum


class PIIType(Enum):
    NIK = 'NIK'
    NISN = 'NISN'
    PHONE = 'PHONE'
    BIOMETRIC = 'BIOMETRIC'
    CREDENTIAL = 'CREDENTIAL'


REDACTED = '[REDACTED]'

# All three patterns delimit on (?<!\d)/(?!\d) rather than \b — \b treats "_"
# and letters as word characters too, so it would miss PII glued directly to
# an identifier (e.g. "nik_3171012345678901"); a digit-only boundary still
# catches that while still refusing to match a substring of a longer run.
# Indonesian NIK (Nomor Induk Kependudukan) is a fixed 16-digit number.
_NIK_RE = re.compile(r'(?<!\d)\d{16}(?!\d)')
# Indonesian mobile numbers: 08xxxxxxxxxx / 628xxxxxxxxxx / +628xxxxxxxxxx.
# Ordered BEFORE the generic 10-digit NISN pattern below — an unscoped
# 10-digit phone (08 + 8 digits) would otherwise get mislabeled as a NISN.
_PHONE_RE = re.compile(r'(?<!\d)(?:\+62|62|08)\d{8,12}(?!\d)')
# Indonesian NISN (Nomor Induk Siswa Nasional) is a fixed 10-digit number.
_NISN_RE = re.compile(r'(?<!\d)\d{10}(?!\d)')

# Ordered tuple, not a dict: detection order matters (NIK before PHONE before
# NISN so a 16-digit NIK's tail never matches PHONE/NISN, and PHONE before
# NISN per the ordering note above).
PII_REGEX_PATTERNS = (
    (PIIType.NIK, _NIK_RE, '[REDACTED_NIK]'),
    (PIIType.PHONE, _PHONE_RE, '[REDACTED_PHONE]'),
    (PIIType.NISN, _NISN_RE, '[REDACTED_NISN]'),
)

# Key-name-detectable types: never a regex shape, detected by dict-key name.
CREDENTIAL_KEYS = frozenset({
    'password', 'token', 'secret', 'authorization', 'cookie',
    'access_token', 'refresh_token', 'pin', 'cvv', 'private_key',
})
# Biometric templates (apps.hardware.BiometricTemplate.template_ciphertext,
# AGENTS Red Line #5) are always stored/transmitted encrypted, but any
# extra={} payload or export column carrying one under these names is still
# redacted.
BIOMETRIC_KEYS = frozenset({
    'biometric', 'biometric_template', 'template_ciphertext', 'face_encoding', 'fingerprint',
})
SENSITIVE_KEYS = CREDENTIAL_KEYS | BIOMETRIC_KEYS


def scrub_string(value: str) -> str:
    for _pii_type, pattern, replacement in PII_REGEX_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub_value(value, key=None):
    """Recursively redact PII/secrets from a value, string, dict, or list.

    A NIK/NISN logged from an IntegerField (or any other non-str/dict/list/
    tuple object, e.g. a model instance whose __str__ embeds PII) would
    otherwise reach the output untouched — every such value is rendered to
    text and run through the same string scrub as a fallback, unless it's
    already a JSON-safe primitive with no PII shape (bool/int/float/None).
    """
    if key is not None and str(key).lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, dict):
        return {k: scrub_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v) for v in value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        scrubbed = scrub_string(str(value))
        return scrubbed if scrubbed != str(value) else value
    if isinstance(value, (float, complex)):
        return value
    scrubbed_repr = scrub_string(str(value))
    return scrubbed_repr if scrubbed_repr != str(value) else value

"""Structured JSON logging with universal PII scrubbing.

Enforces AGENTS Red Line #5 / spec/appendix §2.8: "No PII in Logs or Exports:
Passwords, NIK, NISN, phone numbers, and biometric data must never appear in
raw application logs, unencrypted exports, or error traces." Every configured
handler runs log records through ``PIIScrubbingFilter`` before ``JSONFormatter``
renders them, and ``sentry_before_send`` reuses the exact same ``scrub_value``
engine so external telemetry (Sentry) never receives unmasked PII either.
"""
import json
import logging
import re
import traceback
from datetime import datetime, timezone as dt_timezone

REDACTED = '[REDACTED]'

# All three patterns delimit on (?<!\d)/(?!\d) rather than \b — \b treats "_"
# and letters as word characters too, so it would miss PII glued directly to
# an identifier (e.g. "nik_3171012345678901"); a digit-only boundary still
# catches that while still refusing to match a substring of a longer run.
# Indonesian NIK (Nomor Induk Kependudukan) is a fixed 16-digit number.
_NIK_RE = re.compile(r'(?<!\d)\d{16}(?!\d)')
# Indonesian mobile numbers: 08xxxxxxxxxx / 628xxxxxxxxxx / +628xxxxxxxxxx.
# Applied BEFORE the generic 10-digit NISN pattern below — an unscoped 10-digit
# phone (08 + 8 digits) would otherwise get mislabeled as a NISN.
_PHONE_RE = re.compile(r'(?<!\d)(?:\+62|62|08)\d{8,12}(?!\d)')
# Indonesian NISN (Nomor Induk Siswa Nasional) is a fixed 10-digit number.
_NISN_RE = re.compile(r'(?<!\d)\d{10}(?!\d)')

_SENSITIVE_KEYS = frozenset({
    'password', 'token', 'secret', 'authorization', 'cookie',
    'access_token', 'refresh_token', 'pin', 'cvv',
    # Biometric templates (apps.hardware.BiometricTemplate.template_ciphertext,
    # AGENTS Red Line #5) are always stored/transmitted encrypted, but any
    # extra={} payload carrying one under these names is still redacted.
    'biometric', 'biometric_template', 'template_ciphertext', 'face_encoding', 'fingerprint',
})

# Attributes every stdlib LogRecord carries — diffed against a record's
# __dict__ to find the extra={} payload keys a caller actually added, without
# hardcoding (and risking drift from) the private LogRecord attribute list.
_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord('', 0, '', 0, '', None, None).__dict__.keys())


def _scrub_string(value):
    value = _NIK_RE.sub('[REDACTED_NIK]', value)
    value = _PHONE_RE.sub('[REDACTED_PHONE]', value)
    value = _NISN_RE.sub('[REDACTED_NISN]', value)
    return value


def scrub_value(value, key=None):
    """Recursively redact PII/secrets from a log value, string, dict, or list.

    A NIK/NISN logged from an IntegerField (or any other non-str/dict/list/
    tuple object, e.g. a model instance whose __str__ embeds PII) would
    otherwise reach the log output untouched — every such value is rendered
    to text and run through the same string scrub as a fallback, unless it's
    already a JSON-safe primitive with no PII shape (bool/int/float/None).
    """
    if key is not None and str(key).lower() in _SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: scrub_value(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v) for v in value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        scrubbed = _scrub_string(str(value))
        return scrubbed if scrubbed != str(value) else value
    if isinstance(value, (float, complex)):
        return value
    scrubbed_repr = _scrub_string(str(value))
    return scrubbed_repr if scrubbed_repr != str(value) else value


class PIIScrubbingFilter(logging.Filter):
    """Scrubs every log record's message, args, extras, and traceback in place."""

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = _scrub_string(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(scrub_value(arg) for arg in record.args)
            else:
                record.args = scrub_value(record.args)

        for attr_name in list(record.__dict__.keys()):
            if attr_name in _RESERVED_RECORD_ATTRS:
                continue
            setattr(record, attr_name, scrub_value(getattr(record, attr_name), key=attr_name))

        if record.exc_info:
            # Format now (this is the only place local-frame values would leak
            # into the text) and scrub it, then drop exc_info so no downstream
            # handler/formatter re-renders the raw, unscrubbed traceback.
            raw_traceback = ''.join(traceback.format_exception(*record.exc_info))
            record.exc_text = _scrub_string(raw_traceback)
            record.exc_info = None

        return True


# Fields every access-log line carries when logged with extra={...} — kept
# first and in this order in the JSON output for consistent log-shipper parsing.
_KNOWN_EXTRA_FIELDS = (
    'foundation_id', 'user_id', 'request_id', 'path', 'method', 'status_code', 'duration_ms',
)
_JSON_RESERVED = _RESERVED_RECORD_ATTRS | {'message', 'asctime'}


class JSONFormatter(logging.Formatter):
    """Renders a log record as one machine-parseable JSON line."""

    def format(self, record):
        payload = {
            'timestamp': datetime.fromtimestamp(record.created, tz=dt_timezone.utc)
                .isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        record_dict = record.__dict__
        for field in _KNOWN_EXTRA_FIELDS:
            if field in record_dict:
                payload[field] = record_dict[field]
        for key, value in record_dict.items():
            if key in _JSON_RESERVED or key in _KNOWN_EXTRA_FIELDS or key.startswith('_'):
                continue
            payload[key] = value

        exc_text = getattr(record, 'exc_text', None)
        if exc_text:
            payload['exception'] = exc_text

        return json.dumps(payload, default=str, ensure_ascii=False)


def sentry_before_send(event, hint):
    """Sentry SDK ``before_send`` hook — scrubs the whole event with the same engine."""
    return scrub_value(event)

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
import traceback
from datetime import datetime, timezone as dt_timezone

from apps.core.pii import scrub_string as _scrub_string
from apps.core.pii import scrub_value

# Attributes every stdlib LogRecord carries — diffed against a record's
# __dict__ to find the extra={} payload keys a caller actually added, without
# hardcoding (and risking drift from) the private LogRecord attribute list.
_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord('', 0, '', 0, '', None, None).__dict__.keys())


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

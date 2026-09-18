"""Partner API business logic (spec/18).

Key issue/rotation/revocation, event recording, and webhook delivery.
Event emission for partner-visible domain moments is fire-and-forget from the
emitting app's perspective: `safe_emit_partner_event` never raises into the
caller's transaction — a webhook that cannot be recorded must not roll back a
payment settlement or a student status transition.
"""
import json
import logging
import secrets
import string
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.services import enqueue_task
from apps.partners.crypto import encrypt_secret
from apps.partners.models import PartnerApiKey, PartnerEvent, PartnerWebhookEndpoint

logger = logging.getLogger(__name__)

MAX_ACTIVE_KEYS_PER_FOUNDATION = 2  # PVA-012
ROTATION_OVERLAP_DAYS = 30
READ_ONLY_AFTER_DAYS = 23  # 7-day write freeze before full expiry

EVENT_PAYROLL_RUN_APPROVED = 'payroll.run.approved'
EVENT_FINANCE_PAYMENT_SETTLED = 'finance.payment.settled'
EVENT_ROSTER_STUDENT_ENROLLED = 'roster.student.enrolled'
EVENT_INTEGRATION_KEY_ROTATED = 'integration.key.rotated'

# Retry schedule per spec §6: 5 attempts, exponential backoff, total span <= 6h.
# Delay after attempt N fails (minutes): 1, 5, 30, 120, 150 (total ~6h10m incl. jitter-free).
DELIVERY_RETRY_DELAYS_MINUTES = [1, 5, 30, 120, 150]
MAX_DELIVERY_ATTEMPTS = 5
DELIVERY_TIMEOUT_SECONDS = 5  # partner must reply 2xx within 5s


class KeyLimitExceeded(Exception):
    """More than MAX_ACTIVE_KEYS_PER_FOUNDATION active keys would exist."""


def generate_key_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    body = ''.join(secrets.choice(alphabet) for _ in range(10))
    return f"ak_live_{body}"


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def issue_api_key(foundation_id, label, scopes, school_ids=None, ip_allowlist=None,
                  created_by='') -> tuple[PartnerApiKey, str]:
    """Issue a new key. Returns (key, plaintext_secret) — the ONLY moment the
    secret is ever visible to anyone."""
    if not scopes:
        raise ValueError("At least one scope is required.")
    invalid = [s for s in scopes if s not in PartnerApiKey.ALLOWED_SCOPES]
    if invalid:
        raise ValueError(f"Unknown scopes: {', '.join(invalid)}")
    if PartnerApiKey.SCOPE_PAYROLL_WRITE in scopes and not (ip_allowlist or []):
        raise ValueError("payroll.write requires a non-empty IP allow-list (PVA-013).")

    active_count = PartnerApiKey.all_tenants.filter(
        foundation_id=foundation_id,
        status=PartnerApiKey.STATUS_ACTIVE,
        deleted_at__isnull=True,
    ).count()
    if active_count >= MAX_ACTIVE_KEYS_PER_FOUNDATION:
        raise KeyLimitExceeded(
            f"Foundation already has {active_count} active keys; at most "
            f"{MAX_ACTIVE_KEYS_PER_FOUNDATION} are allowed (PVA-012).")

    secret = generate_secret()
    key = PartnerApiKey.all_tenants.create(
        foundation_id=foundation_id,
        label=label,
        key_id=generate_key_id(),
        secret_encrypted=encrypt_secret(secret),
        scopes=scopes,
        school_ids=school_ids or [],
        ip_allowlist=ip_allowlist or [],
        created_by=created_by,
    )
    return key, secret


def issue_api_key_audited(foundation_id, label, scopes, school_ids=None, ip_allowlist=None,
                          created_by='') -> tuple[PartnerApiKey, str]:
    """issue_api_key plus the `integration.key.issued` audit event — the entry
    point for operator-initiated issuance (JSON admin API and web console).
    rotate_api_key calls the bare issue_api_key instead and audits the
    rotation as its own event."""
    from apps.core.services import audit

    key, secret = issue_api_key(
        foundation_id=foundation_id, label=label, scopes=scopes, school_ids=school_ids,
        ip_allowlist=ip_allowlist, created_by=created_by,
    )
    audit(
        action='integration.key.issued',
        entity_type='PartnerApiKey',
        entity_id=key.key_id,
        foundation_id=foundation_id,
        actor_id=created_by or None,
        diff={'label': label, 'scopes': scopes},
    )
    return key, secret


def rotate_api_key(old_key: PartnerApiKey, created_by: str = '') -> tuple[PartnerApiKey, str]:
    """PVA-012: issue a successor; the old key becomes read-only on day 23
    and stops working entirely on day 30. The old key is never hard-deleted."""
    from apps.core.services import audit

    now = timezone.now()
    new_key, secret = issue_api_key(
        foundation_id=old_key.foundation_id,
        label=old_key.label,
        scopes=old_key.scopes,
        school_ids=old_key.school_ids,
        ip_allowlist=old_key.ip_allowlist,
        created_by=created_by,
    )
    PartnerApiKey.all_tenants.filter(pk=new_key.pk).update(rotated_from_id=old_key.pk)
    new_key.refresh_from_db()

    old_key.read_only_at = now + timedelta(days=READ_ONLY_AFTER_DAYS)
    old_key.expires_at = now + timedelta(days=ROTATION_OVERLAP_DAYS)
    old_key.updated_by = created_by or None
    old_key.save(update_fields=['read_only_at', 'expires_at', 'updated_at', 'updated_by'])

    audit(
        action='integration.key.rotated',
        entity_type='PartnerApiKey',
        entity_id=old_key.key_id,
        foundation_id=old_key.foundation_id,
        actor_id=created_by or None,
        diff={'new_key_id': new_key.key_id},
    )
    safe_emit_partner_event(
        foundation_id=old_key.foundation_id,
        event_type=EVENT_INTEGRATION_KEY_ROTATED,
        payload={
            'rotated_key_id': old_key.key_id,
            'new_key_id': new_key.key_id,
            'read_only_at': old_key.read_only_at.isoformat(),
            'expires_at': old_key.expires_at.isoformat(),
        },
    )
    return new_key, secret


def revoke_api_key(key: PartnerApiKey, revoked_by: str = '') -> PartnerApiKey:
    key.status = PartnerApiKey.STATUS_REVOKED
    key.updated_by = revoked_by or None
    key.save(update_fields=['status', 'updated_at', 'updated_by'])

    from apps.core.services import audit
    audit(
        action='integration.key.revoked',
        entity_type='PartnerApiKey',
        entity_id=key.key_id,
        foundation_id=key.foundation_id,
        actor_id=revoked_by or None,
    )
    return key


def emit_partner_event(foundation_id: int, event_type: str, payload: dict) -> PartnerEvent:
    """Record a partner-visible event and enqueue its webhook delivery via
    core.TaskQueue (drained every minute by cron). The event row is ALWAYS
    created before delivery is scheduled so polling can never miss it (§6).
    """
    event = PartnerEvent.all_tenants.create(
        foundation_id=foundation_id,
        event_type=event_type,
        payload=payload,
        status=PartnerEvent.STATUS_PENDING,
        next_attempt_at=timezone.now(),
    )
    enqueue_task(
        task_type='partners.webhook.deliver',
        payload={'event_id': event.id},
        foundation_id=event.foundation_id,
        run_after=timezone.now(),
    )
    return event


def safe_emit_partner_event(foundation_id: int, event_type: str, payload: dict) -> None:
    """Fire-and-forget wrapper for cross-app callers: never raises."""
    try:
        emit_partner_event(foundation_id, event_type, payload)
    except Exception:
        logger.exception("Failed to record partner event %s for foundation %s",
                         event_type, foundation_id)


def _json_compact(obj) -> str:
    return json.dumps(obj, separators=(',', ':'))


def deliver_partner_webhook(event_id: int) -> dict:
    """TaskQueue handler body (`partners.webhook.deliver`): POST the event to
    the foundation's active endpoint, signed the same way as incoming partner
    requests (t=,v1= over timestamp:method:path:body).

    - 2xx -> DELIVERED.
    - non-2xx/timeout/conn-error -> re-enqueue with exponential backoff up to
      MAX_DELIVERY_ATTEMPTS, then EXHAUSTED. The event row always stays
      pollable via GET /partner/events?since= (spec §9 AC#4).
    - No active endpoint -> event stays PENDING (no retry burn).
    """
    event = PartnerEvent.all_tenants.filter(pk=event_id).first()
    if event is None:
        return {'status': 'gone'}
    if event.status == PartnerEvent.STATUS_DELIVERED:
        return {'status': 'already_delivered'}

    endpoint = PartnerWebhookEndpoint.all_tenants.filter(
        foundation_id=event.foundation_id, is_active=True,
    ).order_by('-updated_at').first()
    if endpoint is None:
        return {'status': 'no_endpoint'}

    from urllib.parse import urlparse
    from apps.partners.crypto import decrypt_secret
    from apps.partners.authentication import compute_signature

    parsed = urlparse(endpoint.url)
    path = parsed.path or '/'
    timestamp = str(int(time.time()))
    body = _json_compact({
        'id': event.id,
        'event_type': event.event_type,
        'occurred_at': event.created_at.isoformat(),
        'payload': event.payload,
    })
    secret = decrypt_secret(endpoint.signing_secret_encrypted)
    signature = compute_signature(secret, timestamp, 'POST', path, body.encode())

    import requests
    event.attempts += 1
    event.last_attempt_at = timezone.now()
    try:
        resp = requests.post(
            endpoint.url,
            data=body,
            headers={
                'Content-Type': 'application/json',
                'X-EduCore-Signature': f't={timestamp},v1={signature}',
            },
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        if 200 <= resp.status_code < 300:
            event.status = PartnerEvent.STATUS_DELIVERED
            event.delivered_at = timezone.now()
            event.last_error = ''
            event.next_attempt_at = None
            event.save()
            return {'status': 'delivered'}
        error_text = f"HTTP {resp.status_code}"
    except Exception as exc:  # timeout, connection error
        error_text = str(exc)[:500]

    event.last_error = error_text
    if event.attempts >= MAX_DELIVERY_ATTEMPTS:
        event.status = PartnerEvent.STATUS_EXHAUSTED
        event.next_attempt_at = None
        event.save()
        return {'status': 'exhausted', 'attempts': event.attempts}

    delay = DELIVERY_RETRY_DELAYS_MINUTES[min(event.attempts, len(DELIVERY_RETRY_DELAYS_MINUTES)) - 1]
    event.next_attempt_at = timezone.now() + timedelta(minutes=delay)
    event.save(update_fields=['attempts', 'last_attempt_at', 'next_attempt_at',
                              'last_error', 'status', 'updated_at'])
    # Retry scheduling is this function's own backoff; max_attempts=1 keeps
    # TaskQueue's own exponential-retry machinery out of the double-retry path.
    enqueue_task(
        task_type='partners.webhook.deliver',
        payload={'event_id': event.id},
        foundation_id=event.foundation_id,
        run_after=event.next_attempt_at,
        max_attempts=1,
    )
    return {'status': 'retrying', 'attempts': event.attempts}


def register_webhook_endpoint(foundation_id: int, url: str, environment: str = 'LIVE',
                              created_by: str = '') -> tuple[PartnerWebhookEndpoint, str]:
    """Register (or replace) the partner's receiving endpoint. Exactly one
    active endpoint per (foundation, environment). Returns (endpoint,
    plaintext_signing_secret) — the secret is shown once."""
    if not url.lower().startswith('https://'):
        raise ValueError("Webhook URL must be HTTPS.")
    with transaction.atomic():
        PartnerWebhookEndpoint.all_tenants.filter(
            foundation_id=foundation_id, environment=environment, is_active=True,
        ).update(is_active=False)
        secret = generate_secret()
        endpoint = PartnerWebhookEndpoint.all_tenants.create(
            foundation_id=foundation_id,
            url=url,
            environment=environment,
            signing_secret_encrypted=encrypt_secret(secret),
            is_active=True,
            created_by=created_by,
        )
    return endpoint, secret

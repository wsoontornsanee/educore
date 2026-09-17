"""Calendar sync services: OAuth state, token lifecycle, event ingestion.

All functions scope through thread-local foundation context (TenantManager)
or explicit tenant_context — no .all_tenants outside the cron command.
"""
import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .crypto import decrypt_secret, encrypt_secret
from .models import CalendarConnection, ExternalCalendarEvent

# Refresh an access token this long before its expiry to avoid using a
# token that expires mid-flight.
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)

# Incremental pull window when no cursor/window is recorded (and the lookback
# cap for "sync from" on every run — events older than this never import).
SYNC_LOOKBACK_DAYS = 30
SYNC_FORWARD_DAYS = 90

# HMAC-signed OAuth state binds {user_id, foundation_id, provider, nonce} so
# the public callback cannot be tricked into storing a connection under the
# wrong user/foundation (state forgery / login-CSRF).
STATE_MAX_AGE_SECONDS = 600

DEFAULT_REDIRECT_URI = 'http://localhost:8000/web/auth/calendar/callback/'


def get_redirect_uri() -> str:
    """Registered OAuth redirect URI — one shared path for both providers;
    the provider rides in the signed state, so registrars only need to
    whitelist a single URL per provider console."""
    return getattr(settings, 'CALENDAR_SYNC_REDIRECT_URI', '') or DEFAULT_REDIRECT_URI


class OAuthStateError(Exception):
    """Signed state missing, expired, tampered, or bound to another user."""


def sign_state(payload: dict) -> str:
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    sig = hmac.new(
        str(settings.SECRET_KEY).encode(), body.encode(), hashlib.sha256,
    ).hexdigest()
    return f"{body}.{sig}"


def verify_state(state: str, user_id: int, foundation_id) -> dict:
    try:
        body, sig = state.rsplit('.', 1)
    except (ValueError, AttributeError):
        raise OAuthStateError("Malformed state.")
    expected = hmac.new(
        str(settings.SECRET_KEY).encode(), body.encode(), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise OAuthStateError("State signature mismatch.")
    payload = json.loads(body)
    if payload.get('user_id') != user_id or payload.get('foundation_id') != foundation_id:
        raise OAuthStateError("State bound to a different user/foundation.")
    issued = payload.get('issued_at', 0)
    if timezone.now().timestamp() - issued > STATE_MAX_AGE_SECONDS:
        raise OAuthStateError("State expired.")
    return payload


def verify_state_unbound(state: str) -> dict:
    """Callback-side state check: signature + expiry only.

    The callback route is unauthenticated, so there is no caller to bind
    against — the signature itself is the proof the state was minted by the
    connect-start endpoint, and it covers user_id/foundation_id/provider.
    """
    try:
        body, sig = state.rsplit('.', 1)
    except (ValueError, AttributeError):
        raise OAuthStateError("Malformed state.")
    expected = hmac.new(
        str(settings.SECRET_KEY).encode(), body.encode(), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise OAuthStateError("State signature mismatch.")
    payload = json.loads(body)
    if timezone.now().timestamp() - payload.get('issued_at', 0) > STATE_MAX_AGE_SECONDS:
        raise OAuthStateError("State expired.")
    return payload


def store_tokens_from_exchange(connection: CalendarConnection, tokens: dict) -> None:
    """Persist an exchange/refresh result onto a connection (encryption here,
    never at call sites)."""
    connection.access_token_encrypted = encrypt_secret(tokens['access_token'])
    if tokens.get('refresh_token'):
        # Providers may omit refresh_token on refresh responses — keep the stored one.
        connection.refresh_token_encrypted = encrypt_secret(tokens['refresh_token'])
    connection.token_expires_at = timezone.now() + timedelta(seconds=tokens.get('expires_in', 3600))
    if tokens.get('provider_email'):
        connection.provider_email = tokens['provider_email']
    connection.status = CalendarConnection.STATUS_CONNECTED
    connection.last_error = ''
    connection.save(update_fields=[
        'access_token_encrypted', 'refresh_token_encrypted', 'token_expires_at',
        'provider_email', 'status', 'last_error', 'updated_at',
    ])


def get_valid_access_token(connection: CalendarConnection) -> str:
    """Return a usable access token, refreshing when inside the margin.

    On invalid_grant (user revoked at the provider), the connection is marked
    REVOKED and OAuthStateError-shaped TokenExchangeError propagates — the cron
    must never crash-loop on a dead connection.
    """
    from .providers import TokenExchangeError

    if connection.token_expires_at and connection.token_expires_at - TOKEN_REFRESH_MARGIN > timezone.now():
        return decrypt_secret(connection.access_token_encrypted)

    refresh_token = decrypt_secret(connection.refresh_token_encrypted)
    if not refresh_token:
        connection.status = CalendarConnection.STATUS_ERROR
        connection.last_error = 'No refresh token stored; reconnect required.'
        connection.save(update_fields=['status', 'last_error', 'updated_at'])
        raise TokenExchangeError(connection.last_error)

    provider = _provider_for_connection(connection)
    try:
        tokens = provider.refresh_tokens(refresh_token)
    except TokenExchangeError as exc:
        if exc.invalid_grant:
            connection.status = CalendarConnection.STATUS_REVOKED
            connection.last_error = 'Refresh token rejected by provider (revoked or expired).'
            connection.save(update_fields=['status', 'last_error', 'updated_at'])
        raise
    store_tokens_from_exchange(connection, tokens)
    return tokens['access_token']


def _provider_for_connection(connection: CalendarConnection):
    from .providers import get_provider
    return get_provider(connection.provider, get_redirect_uri())


def _parse_provider_event(raw: dict) -> dict:
    """Copy only model-shaped fields out of a provider event dict."""
    return {
        'title': raw.get('title', '')[:512],
        'description': raw.get('description', ''),
        'location': raw.get('location', '')[:512],
        'start_at': raw['start_at'],
        'end_at': raw['end_at'],
        'is_all_day': bool(raw.get('is_all_day')),
        'meeting_url': raw.get('meeting_url', '')[:1024],
        'is_cancelled': bool(raw.get('is_cancelled')),
    }


def ingest_events(connection: CalendarConnection, events: list[dict]) -> dict:
    """Upsert provider events idempotently per (connection, provider_event_id)."""
    created = updated = skipped = 0
    for raw in events:
        event_id = raw.get('id')
        if not event_id or not raw.get('start_at') or not raw.get('end_at'):
            skipped += 1
            continue
        fields = _parse_provider_event(raw)
        obj, was_created = ExternalCalendarEvent.objects.update_or_create(
            connection=connection, provider_event_id=event_id,
            defaults={**fields, 'foundation_id': connection.foundation_id},
        )
        if was_created:
            created += 1
        else:
            updated += 1
    return {'created': created, 'updated': updated, 'skipped': skipped}


def sync_connection(connection: CalendarConnection) -> dict:
    """Pull one connection's events for the sync window and ingest them.

    A provider failure marks the connection ERROR and returns the error info
    instead of raising — one dead calendar never aborts the sweep.
    """
    from .providers import TokenExchangeError

    now = timezone.now()
    try:
        access_token = get_valid_access_token(connection)
        provider = _provider_for_connection(connection)
        events = provider.list_events(
            access_token, connection.calendar_id,
            now - timedelta(days=SYNC_LOOKBACK_DAYS),
            now + timedelta(days=SYNC_FORWARD_DAYS),
        )
    except TokenExchangeError as exc:
        connection.last_synced_at = now
        connection.last_error = str(exc)[:255]
        if connection.status == CalendarConnection.STATUS_CONNECTED:
            connection.status = CalendarConnection.STATUS_ERROR
        connection.save(update_fields=['last_synced_at', 'last_error', 'status', 'updated_at'])
        return {'ok': False, 'error': str(exc)}

    counts = ingest_events(connection, events)
    connection.last_synced_at = now
    connection.last_error = ''
    connection.status = CalendarConnection.STATUS_CONNECTED
    connection.save(update_fields=['last_synced_at', 'last_error', 'status', 'updated_at'])
    return {'ok': True, **counts}


def disconnect_connection(connection: CalendarConnection) -> None:
    """Soft-delete a connection and soft-delete its imported events (red line 2:
    nothing is hard-deleted; the unique constraint's active marker frees the
    (connection, provider_event_id) key for a future reconnect)."""
    with transaction.atomic():
        ExternalCalendarEvent.objects.filter(
            connection=connection, deleted_at__isnull=True,
        ).update(deleted_at=timezone.now())
        connection.deleted_at = timezone.now()
        connection.save(update_fields=['deleted_at', 'updated_at'])

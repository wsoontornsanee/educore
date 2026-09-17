"""Provider ABC + Google Calendar API and Microsoft Graph implementations.

Both providers live behind one interface so the OAuth flow and the sync cron
never branch on provider specifics. HTTP is via `requests` and is always
mocked in tests (Xendit precedent: assert exact request shape, no real
network in CI).

Endpoints are read from settings so tests can point them at fakes without
monkeypatching module constants:
- GOOGLE_OAUTH_AUTH_URL / GOOGLE_OAUTH_TOKEN_URL / GOOGLE_CALENDAR_API_BASE
- MICROSOFT_OAUTH_AUTH_URL / MICROSOFT_OAUTH_TOKEN_URL / MICROSOFT_GRAPH_API_BASE

Client credentials come from GOOGLE_CALENDAR_CLIENT_ID/SECRET and
MICROSOFT_CALENDAR_CLIENT_ID/SECRET; an empty ID means that provider's
calendar sync is disabled (mirrors the SSO settings pattern) — authorize_url
and exchange_code fail loud with ProviderDisabledError.
"""
import secrets
import urllib.parse
from abc import ABC, abstractmethod
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone


class ProviderDisabledError(Exception):
    """No client credentials configured for this provider."""


class TokenExchangeError(Exception):
    """The provider rejected an authorization-code exchange or token refresh."""

    def __init__(self, message, invalid_grant=False):
        super().__init__(message)
        # invalid_grant=True means the refresh token itself was revoked/
        # expired — the caller should mark the connection REVOKED, not retry.
        self.invalid_grant = invalid_grant


def _settings_or_default(name, default):
    return getattr(settings, name, '') or default


class CalendarProvider(ABC):
    """Interface every calendar provider backend implements."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @classmethod
    @abstractmethod
    def slug(cls) -> str: ...

    @classmethod
    def from_settings(cls, redirect_uri: str) -> 'CalendarProvider':
        conf = PROVIDER_CONFIGS[cls.slug()]
        client_id = _settings_or_default(conf['client_id_setting'], '')
        client_secret = _settings_or_default(conf['client_secret_setting'], '')
        if not client_id:
            raise ProviderDisabledError(
                f"Calendar sync for '{cls.slug()}' is not configured "
                f"({conf['client_id_setting']} is empty)."
            )
        return cls(client_id, client_secret, redirect_uri)

    @abstractmethod
    def authorize_url(self, state: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str) -> dict:
        """Exchange an authorization code for {access_token, refresh_token,
        expires_in, provider_email} (raises TokenExchangeError)."""

    @abstractmethod
    def refresh_tokens(self, refresh_token: str) -> dict:
        """Refresh tokens; returns the same shape as exchange_code."""

    @abstractmethod
    def list_events(self, access_token: str, calendar_id: str,
                    time_min, time_max) -> list[dict]:
        """Return normalized events for [time_min, time_max):
        {id, title, description, location, start_at, end_at,
         is_all_day, meeting_url, is_cancelled} (tz-aware datetimes)."""

    @staticmethod
    def _post_token(url: str, data: dict) -> dict:
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code != 200:
            payload = {}
            try:
                payload = resp.json()
            except ValueError:
                pass
            invalid_grant = payload.get('error') == 'invalid_grant'
            raise TokenExchangeError(
                f"Token endpoint returned HTTP {resp.status_code}: {payload.get('error', 'unknown error')}",
                invalid_grant=invalid_grant,
            )
        return resp.json()


class GoogleCalendarProvider(CalendarProvider):
    """Google Calendar API v3 (oauth2 authorization-code, offline access)."""

    @classmethod
    def slug(cls):
        return 'google'

    def authorize_url(self, state: str) -> str:
        base = _settings_or_default('GOOGLE_OAUTH_AUTH_URL', 'https://accounts.google.com/o/oauth2/v2/auth')
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'openid email https://www.googleapis.com/auth/calendar.readonly',
            'access_type': 'offline',
            'prompt': 'consent',
            'state': state,
        }
        return f"{base}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        token_url = _settings_or_default('GOOGLE_OAUTH_TOKEN_URL', 'https://oauth2.googleapis.com/token')
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri,
        }
        payload = self._post_token(token_url, data)
        return self._normalize_tokens(payload)

    def refresh_tokens(self, refresh_token: str) -> dict:
        token_url = _settings_or_default('GOOGLE_OAUTH_TOKEN_URL', 'https://oauth2.googleapis.com/token')
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        payload = self._post_token(token_url, data)
        return self._normalize_tokens(payload)

    @staticmethod
    def _normalize_tokens(payload: dict) -> dict:
        return {
            'access_token': payload.get('access_token', ''),
            # Google omits refresh_token on refresh responses — preserve the stored one.
            'refresh_token': payload.get('refresh_token', ''),
            'expires_in': int(payload.get('expires_in', 3600)),
            'provider_email': payload.get('email', ''),
        }

    def list_events(self, access_token: str, calendar_id: str, time_min, time_max) -> list[dict]:
        base = _settings_or_default('GOOGLE_CALENDAR_API_BASE', 'https://www.googleapis.com/calendar/v3')
        url = f"{base}/calendars/{urllib.parse.quote(calendar_id)}/events"
        resp = requests.get(
            url,
            params={
                'timeMin': time_min.isoformat(),
                'timeMax': time_max.isoformat(),
                'singleEvents': 'true',
                'orderBy': 'startTime',
                'maxResults': 2500,
            },
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=30,
        )
        if resp.status_code != 200:
            raise TokenExchangeError(f"Calendar list returned HTTP {resp.status_code}")
        events = []
        for item in resp.json().get('items', []):
            start_raw = item.get('start', {})
            end_raw = item.get('end', {})
            is_all_day = 'date' in start_raw

            def _parse(raw: dict):
                value = raw.get('dateTime') or raw.get('date')
                parsed = timezone.datetime.fromisoformat(value)
                if timezone.is_aware(parsed):
                    return parsed  # dateTime carried an explicit offset (Z or +HH:MM)
                # All-day dates carry no timezone; interpret in local ops timezone.
                return timezone.make_aware(parsed, timezone.get_current_timezone())

            events.append({
                'id': item.get('id', ''),
                'title': item.get('summary', ''),
                'description': item.get('description', ''),
                'location': item.get('location', ''),
                'start_at': _parse(start_raw),
                'end_at': _parse(end_raw),
                'is_all_day': is_all_day,
                'meeting_url': item.get('hangoutLink', ''),
                'is_cancelled': item.get('status') == 'cancelled',
            })
        return events


class MicrosoftGraphCalendarProvider(CalendarProvider):
    """Microsoft Graph calendar API (Entra ID v2.0 endpoints, delegated mail/Calendars.Read)."""

    @classmethod
    def slug(cls):
        return 'microsoft'

    def _tenant(self) -> str:
        # 'common' (multi-tenant) unless per-foundation pinning later narrows it.
        return _settings_or_default('MICROSOFT_CALENDAR_TENANT_ID', 'common')

    def authorize_url(self, state: str) -> str:
        base = _settings_or_default('MICROSOFT_OAUTH_AUTH_URL', 'https://login.microsoftonline.com')
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'offline_access openid email Calendars.Read',
            'state': state,
        }
        return f"{base}/{self._tenant()}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        token_url = _settings_or_default(
            'MICROSOFT_OAUTH_TOKEN_URL', 'https://login.microsoftonline.com')
        token_url = f"{token_url.rstrip('/')}/{self._tenant()}/oauth2/v2.0/token" \
            if '/oauth2' not in token_url else token_url
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri,
        }
        payload = self._post_token(token_url, data)
        return self._normalize_tokens(payload)

    def refresh_tokens(self, refresh_token: str) -> dict:
        token_url = _settings_or_default(
            'MICROSOFT_OAUTH_TOKEN_URL', 'https://login.microsoftonline.com')
        token_url = f"{token_url.rstrip('/')}/{self._tenant()}/oauth2/v2.0/token" \
            if '/oauth2' not in token_url else token_url
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        payload = self._post_token(token_url, data)
        return self._normalize_tokens(payload)

    @staticmethod
    def _normalize_tokens(payload: dict) -> dict:
        return {
            'access_token': payload.get('access_token', ''),
            'refresh_token': payload.get('refresh_token', ''),
            'expires_in': int(payload.get('expires_in', 3600)),
            'provider_email': payload.get('mail', '') or payload.get('email', ''),
        }

    def list_events(self, access_token: str, calendar_id: str, time_min, time_max) -> list[dict]:
        base = _settings_or_default('MICROSOFT_GRAPH_API_BASE', 'https://graph.microsoft.com/v1.0')
        path = f"calendars/{urllib.parse.quote(calendar_id)}/calendarView" \
            if calendar_id != 'primary' else 'me/calendarView'
        url = f"{base}/{path}"
        resp = requests.get(
            url,
            params={
                'startDateTime': time_min.isoformat(),
                'endDateTime': time_max.isoformat(),
                '$top': 1000,
            },
            headers={'Authorization': f'Bearer {access_token}', 'Prefer': 'outlook.timezone="UTC"'},
            timeout=30,
        )
        if resp.status_code != 200:
            raise TokenExchangeError(f"Calendar view returned HTTP {resp.status_code}")
        events = []
        for item in resp.json().get('value', []):
            is_all_day = bool(item.get('isAllDay'))

            def _parse(raw: dict):
                # Graph returns naive local datetime strings + a timeZone name;
                # the Prefer: outlook.timezone="UTC" header makes them UTC.
                value = raw['dateTime']
                if not value.endswith('Z'):
                    value += 'Z'
                return timezone.datetime.fromisoformat(value)

            online_join = (item.get('onlineMeeting') or {}).get('joinUrl', '')
            # Teams-generated events also carry a join link in the location.
            location = item.get('location', {}).get('displayName', '') if isinstance(item.get('location'), dict) else ''
            if not online_join:
                loc_url = (item.get('locations') or [{}])[0].get('locationUri', '') if isinstance(item.get('locations'), list) else ''
                online_join = loc_url if loc_url and loc_url.startswith('http') else ''
            events.append({
                'id': item.get('id', ''),
                'title': item.get('subject', ''),
                'description': item.get('bodyPreview', ''),
                'location': location,
                'start_at': _parse(item['start']),
                'end_at': _parse(item['end']),
                'is_all_day': is_all_day,
                'meeting_url': online_join,
                'is_cancelled': item.get('isCancelled', False),
            })
        return events


PROVIDER_CONFIGS = {
    'google': {
        'client_id_setting': 'GOOGLE_CALENDAR_CLIENT_ID',
        'client_secret_setting': 'GOOGLE_CALENDAR_CLIENT_SECRET',
        'provider_class': GoogleCalendarProvider,
    },
    'microsoft': {
        'client_id_setting': 'MICROSOFT_CALENDAR_CLIENT_ID',
        'client_secret_setting': 'MICROSOFT_CALENDAR_CLIENT_SECRET',
        'provider_class': MicrosoftGraphCalendarProvider,
    },
}


def get_provider(slug: str, redirect_uri: str) -> CalendarProvider:
    try:
        conf = PROVIDER_CONFIGS[slug]
    except KeyError:
        raise ValueError(f"Unknown calendar provider: {slug}")
    return conf['provider_class'].from_settings(redirect_uri)


def provider_enabled(slug: str) -> bool:
    try:
        conf = PROVIDER_CONFIGS[slug]
    except KeyError:
        return False
    return bool(_settings_or_default(conf['client_id_setting'], ''))


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)

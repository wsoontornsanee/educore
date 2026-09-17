"""Calendar sync views: connection lifecycle API + OAuth web callback.

The callback is a public (unauthenticated) web route — the only place the
authorization code exists. It trusts nothing but the HMAC-signed state
(bound to user+foundation+provider at connect-start time).
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CalendarConnection, ExternalCalendarEvent
from .providers import (
    PROVIDER_CONFIGS,
    ProviderDisabledError,
    TokenExchangeError,
    generate_oauth_state,
    get_provider,
    provider_enabled,
)
from .services import (
    OAuthStateError,
    disconnect_connection,
    get_redirect_uri,
    sign_state,
    store_tokens_from_exchange,
    verify_state_unbound,
)

PROVIDER_SLUGS = list(PROVIDER_CONFIGS.keys())


def _serialize_connection(conn: CalendarConnection) -> dict:
    return {
        'provider': conn.provider,
        'provider_email': conn.provider_email,
        'calendar_id': conn.calendar_id,
        'status': conn.status,
        'last_error': conn.last_error,
        'last_synced_at': conn.last_synced_at,
        'token_expires_at': conn.token_expires_at,
    }


class CalendarConnectionView(APIView):
    """GET /api/v1/auth/calendar/connection/ — the caller's own connections.

    DELETE /api/v1/auth/calendar/connection/?provider=<slug> — soft-delete
    the caller's connection for that provider (and its imported events).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        conns = CalendarConnection.objects.filter(user=request.user)
        return Response({
            'results': [_serialize_connection(c) for c in conns],
            'providers_enabled': [s for s in PROVIDER_SLUGS if provider_enabled(s)],
        })

    def delete(self, request):
        provider = request.query_params.get('provider', '')
        if provider not in PROVIDER_SLUGS:
            return Response({'error': 'Unknown provider.', 'code': 'PROVIDER_UNKNOWN'},
                            status=status.HTTP_400_BAD_REQUEST)
        conn = CalendarConnection.objects.filter(user=request.user, provider=provider).first()
        if not conn:
            return Response({'error': 'No connection for this provider.', 'code': 'NOT_FOUND'},
                            status=status.HTTP_404_NOT_FOUND)
        disconnect_connection(conn)
        from apps.core.services import audit
        audit(
            action='identity.calendar.disconnected', entity_type='CalendarConnection',
            entity_id=str(conn.id), actor_id=str(request.user.id),
            foundation_id=conn.foundation_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalendarConnectStartView(APIView):
    """GET /api/v1/auth/calendar/connect/<provider>/start/ — returns the
    provider's OAuth authorize URL with an HMAC-signed state binding the
    caller's user + foundation. The browser opens it; the provider returns
    to the public web callback.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, provider):
        if provider not in PROVIDER_SLUGS:
            return Response({'error': 'Unknown provider.', 'code': 'PROVIDER_UNKNOWN'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            provider_obj = get_provider(provider, get_redirect_uri())
        except ProviderDisabledError as exc:
            return Response({'error': str(exc), 'code': 'PROVIDER_DISABLED'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        state = sign_state({
            'user_id': request.user.id,
            'foundation_id': getattr(request, 'foundation_id', None)
                or request.headers.get('X-Foundation-ID'),
            'provider': provider,
            'nonce': generate_oauth_state(),
            'issued_at': timezone.now().timestamp(),
        })
        return Response({'authorize_url': provider_obj.authorize_url(state), 'state': state})


class CalendarOAuthCallbackView(APIView):
    """GET /web/auth/calendar/callback/?code=&state= — public OAuth redirect
    target. Exchanges the code, stores encrypted tokens, and renders a small
    HTML confirmation (the window may be closed). Never authenticated: the
    signed state is the sole proof of who initiated the flow.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from apps.identity.models import User

        error = request.query_params.get('error')
        if error:
            return self._page(f"Koneksi kalender dibatalkan: {error}", ok=False)

        try:
            payload = verify_state_unbound(request.query_params.get('state', ''))
        except OAuthStateError as exc:
            return self._page(f"Verifikasi state gagal: {exc}", ok=False)

        provider_slug = payload.get('provider', '')
        if provider_slug not in PROVIDER_SLUGS:
            return self._page("Provider tidak dikenal.", ok=False)

        # User is a TenantModel — set the state's foundation context BEFORE
        # the lookup or the fail-closed TenantManager returns DoesNotExist.
        from educore.middleware.tenancy import set_current_foundation_id
        foundation_id = payload.get('foundation_id')
        if foundation_id:
            set_current_foundation_id(int(foundation_id))
        try:
            user = User.objects.get(pk=payload['user_id'])
        except User.DoesNotExist:
            return self._page("Pengguna tidak ditemukan.", ok=False)

        code = request.query_params.get('code', '')
        if not code:
            return self._page("Kode otorisasi tidak ada.", ok=False)

        try:
            provider_obj = get_provider(provider_slug, get_redirect_uri())
            tokens = provider_obj.exchange_code(code)
        except ProviderDisabledError as exc:
            return self._page(str(exc), ok=False)
        except TokenExchangeError as exc:
            return self._page(f"Pertukaran token gagal: {exc}", ok=False)

        conn, _ = CalendarConnection.objects.update_or_create(
            user=user, provider=provider_slug,
            defaults={
                'foundation_id': int(foundation_id) if foundation_id else user.foundation_id,
                'calendar_id': 'primary',
                'status': CalendarConnection.STATUS_CONNECTED,
            },
        )
        store_tokens_from_exchange(conn, tokens)
        from apps.core.services import audit
        audit(
            action='identity.calendar.connected', entity_type='CalendarConnection',
            entity_id=str(conn.id), actor_id=str(user.id),
            foundation_id=conn.foundation_id,
        )
        return self._page(
            f"Kalender {conn.get_provider_display()} terhubung untuk "
            f"{conn.provider_email or user.email}. Anda bisa menutup jendela ini.",
            ok=True,
        )

    @staticmethod
    def _page(message: str, ok: bool) -> 'HttpResponse':
        from django.http import HttpResponse
        color = '#1a7f37' if ok else '#c8102e'
        html = f"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>Sinkronisasi Kalender</title></head>
<body style="font-family: system-ui, sans-serif; padding: 2rem;">
<h2 style="color: {color};">{'✓ Berhasil' if ok else '✗ Gagal'}</h2>
<p>{message}</p>
</body></html>"""
        return HttpResponse(html, content_type='text/html; charset=utf-8')


class CalendarEventListView(APIView):
    """GET /api/v1/auth/calendar/events/?provider=&from=&to= — the caller's
    own imported events (a staff member reads their calendar's events, never
    another user's)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = ExternalCalendarEvent.objects.filter(
            connection__user=request.user, connection__deleted_at__isnull=True,
        ).order_by('start_at')

        provider = request.query_params.get('provider')
        if provider:
            qs = qs.filter(connection__provider=provider)

        from_raw = parse_date(request.query_params.get('from', '') or '')
        to_raw = parse_date(request.query_params.get('to', '') or '')
        now = timezone.now()
        time_min = datetime.combine(from_raw, datetime.min.time(), tzinfo=dt_timezone.utc) if from_raw \
            else now - timedelta(days=30)
        time_max = datetime.combine(to_raw, datetime.max.time(), tzinfo=dt_timezone.utc) if to_raw \
            else now + timedelta(days=90)
        qs = qs.filter(start_at__gte=time_min, start_at__lt=time_max)

        results = [{
            'provider': e.connection.provider,
            'provider_event_id': e.provider_event_id,
            'title': e.title,
            'description': e.description,
            'location': e.location,
            'start_at': e.start_at,
            'end_at': e.end_at,
            'is_all_day': e.is_all_day,
            'meeting_url': e.meeting_url,
            'is_cancelled': e.is_cancelled,
        } for e in qs[:500]]
        return Response({'results': results, 'count': len(results)})

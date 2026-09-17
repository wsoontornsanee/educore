"""Tests for calendar sync (spec/14 §6, second purpose of the Google/Microsoft integration).

Covers: token encryption at rest, HMAC-signed OAuth state (forgery/expiry/
tamper rejection), connect-start endpoint (auth + provider-disabled),
public callback (exchange, token storage, duplicate connect), token refresh
(including revoked-refresh-token -> connection REVOKED), the sync cron
(idempotency, per-connection failure isolation, JobRun), the read API
(caller-scoped, date filtering), disconnect (soft-delete of connection and
events), and cross-tenant isolation. All provider HTTP is mocked — no real
network calls in CI.
"""
import json
import unittest.mock
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.calendar_sync.crypto import decrypt_secret, encrypt_secret
from apps.calendar_sync.models import CalendarConnection, ExternalCalendarEvent
from apps.calendar_sync.providers import (
    ProviderDisabledError,
    TokenExchangeError,
    get_provider,
)
from apps.calendar_sync.services import (
    OAuthStateError,
    get_valid_access_token,
    ingest_events,
    sign_state,
    sync_connection,
    verify_state,
    verify_state_unbound,
)
from apps.core.models import JobRun
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import (
    clear_current_foundation_id,
    set_current_foundation_id,
    tenant_context,
)

FAKE_STATE = sign_state({
    'user_id': 1, 'foundation_id': 1, 'provider': 'google',
    'nonce': 'n', 'issued_at': timezone.now().timestamp(),
})


def _google_provider_response(events):
    return {'items': events}


def _g_event(id_='evt-1', summary='Ujian Matematika', start='2026-09-20T08:00:00Z',
             end='2026-09-20T09:30:00Z', status='confirmed', hangout=''):
    return {
        'id': id_, 'summary': summary, 'status': status,
        'start': {'dateTime': start}, 'end': {'dateTime': end},
        'hangoutLink': hangout,
    }


def _ms_event(id_='ms-evt-1', subject='Rapat Yayasan', start='2026-09-21T02:00:00.0000000',
              end='2026-09-21T03:00:00.0000000', cancelled=False, join_url=''):
    event = {
        'id': id_, 'subject': subject, 'isAllDay': False, 'isCancelled': cancelled,
        'bodyPreview': '', 'location': {'displayName': 'Ruang Rapat'},
        'start': {'dateTime': start, 'timeZone': 'UTC'},
        'end': {'dateTime': end, 'timeZone': 'UTC'},
    }
    if join_url:
        event['onlineMeeting'] = {'joinUrl': join_url}
    return event


class BaseCalendarTestCase(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Kalender", brand_name="Kalender",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Kal", npsn="20419888",
            level=School.LEVEL_SMA,
        )
        set_current_foundation_id(self.foundation.id)
        self.client = APIClient()
        self.staff_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+628123459001",
            email="guru.kal@sekolah.sch.id",
            full_name="Guru Kalender",
        )
        assign_role(
            user=self.staff_user, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain",
        )
        self.other_user = User.objects.create(
            foundation_id=self.other_foundation.id,
            phone_e164="+628123459002",
            email="guru.lain@sekolah.sch.id",
            full_name="Guru Lain",
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _connection(self, user=None, foundation_id=None, provider='google',
                    status=CalendarConnection.STATUS_CONNECTED):
        user = user or self.staff_user
        foundation_id = foundation_id or user.foundation_id
        return CalendarConnection.all_tenants.create(
            foundation_id=foundation_id, user=user, provider=provider,
            access_token_encrypted=encrypt_secret('at-old'),
            refresh_token_encrypted=encrypt_secret('rt-old'),
            token_expires_at=timezone.now() + timedelta(hours=1),
            status=status,
        )


class TokenEncryptionTest(BaseCalendarTestCase):
    def test_tokens_encrypted_at_rest(self):
        conn = self._connection()
        stored = CalendarConnection.all_tenants.get(pk=conn.pk).access_token_encrypted
        self.assertNotIn('at-old', stored)
        self.assertEqual(decrypt_secret(stored), 'at-old')

    def test_decrypt_rejects_wrong_key(self):
        from django.test import override_settings
        ciphertext = encrypt_secret('secret-value')
        with override_settings(EDUCORE_CALENDAR_FERNET_KEY='other-key-material'):
            with self.assertRaises(RuntimeError):
                decrypt_secret(ciphertext)


class OAuthStateTest(BaseCalendarTestCase):
    def test_roundtrip_and_expiry(self):
        from apps.calendar_sync.services import STATE_MAX_AGE_SECONDS
        state = sign_state({'user_id': 7, 'foundation_id': 3, 'provider': 'google',
                            'issued_at': timezone.now().timestamp()})
        payload = verify_state(state, 7, 3)
        self.assertEqual(payload['user_id'], 7)

        stale = sign_state({'user_id': 7, 'foundation_id': 3, 'provider': 'google',
                            'issued_at': timezone.now().timestamp() - STATE_MAX_AGE_SECONDS - 5})
        with self.assertRaises(OAuthStateError):
            verify_state(stale, 7, 3)

    def test_tampered_payload_rejected(self):
        state = sign_state({'user_id': 7, 'foundation_id': 3, 'provider': 'google',
                            'issued_at': timezone.now().timestamp()})
        body, sig = state.rsplit('.', 1)
        forged = body.replace('"user_id":7', '"user_id":8') + '.' + sig
        with self.assertRaises(OAuthStateError):
            verify_state(forged, 8, 3)

    def test_garbage_rejected(self):
        for bad in ('', 'nonsense', 'a.b.c'):
            with self.assertRaises(OAuthStateError):
                verify_state_unbound(bad)


@override_settings(GOOGLE_CALENDAR_CLIENT_ID='cal-client-id',
                   GOOGLE_CALENDAR_CLIENT_SECRET='cal-secret',
                   CALENDAR_SYNC_REDIRECT_URI='http://testserver/web/auth/calendar/callback/')
class ConnectFlowTest(BaseCalendarTestCase):
    def test_connect_start_returns_authorize_url(self):
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/calendar/connect/google/start/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('accounts.google.com/o/oauth2/v2/auth', res.data['authorize_url'])
        self.assertIn('calendar.readonly', res.data['authorize_url'])
        # State must verify and bind this user.
        payload = verify_state(res.data['state'], self.staff_user.id, self.staff_user.foundation_id)
        self.assertEqual(payload['provider'], 'google')

    def test_connect_start_requires_auth(self):
        res = self.client.get('/api/v1/auth/calendar/connect/google/start/')
        self.assertEqual(res.status_code, 401)

    def test_connect_start_unknown_provider(self):
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/calendar/connect/outlook/start/')
        self.assertEqual(res.status_code, 400)

    def test_connect_start_fails_loud_when_disabled(self):
        self.client.force_authenticate(user=self.staff_user)
        with override_settings(GOOGLE_CALENDAR_CLIENT_ID=''):
            res = self.client.get('/api/v1/auth/calendar/connect/google/start/')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data['code'], 'PROVIDER_DISABLED')

    def test_callback_exchanges_code_and_stores_encrypted_tokens(self):
        state = sign_state({
            'user_id': self.staff_user.id,
            'foundation_id': self.foundation.id,
            'provider': 'google',
            'issued_at': timezone.now().timestamp(),
        })
        with unittest.mock.patch.object(
                get_provider('google', '').__class__, 'exchange_code') as m_exchange:
            m_exchange.return_value = {
                'access_token': 'at-new', 'refresh_token': 'rt-new',
                'expires_in': 3600, 'provider_email': 'guru.kal@gmail.com',
            }
            res = self.client.get('/web/auth/calendar/callback/',
                                  {'code': 'auth-code', 'state': state})
        self.assertContains(res, 'Berhasil', status_code=200)
        conn = CalendarConnection.all_tenants.get(user=self.staff_user, provider='google')
        self.assertEqual(conn.status, CalendarConnection.STATUS_CONNECTED)
        self.assertEqual(conn.provider_email, 'guru.kal@gmail.com')
        self.assertNotIn('at-new', conn.access_token_encrypted)
        self.assertEqual(decrypt_secret(conn.access_token_encrypted), 'at-new')

    def test_callback_rejects_forged_state(self):
        res = self.client.get('/web/auth/calendar/callback/',
                              {'code': 'x', 'state': 'tampered.state'})
        self.assertContains(res, 'Gagal', status_code=200)

    def test_callback_rejects_expired_state(self):
        from apps.calendar_sync.services import STATE_MAX_AGE_SECONDS
        state = sign_state({
            'user_id': self.staff_user.id, 'foundation_id': self.foundation.id,
            'provider': 'google',
            'issued_at': timezone.now().timestamp() - STATE_MAX_AGE_SECONDS - 10,
        })
        res = self.client.get('/web/auth/calendar/callback/', {'code': 'x', 'state': state})
        self.assertContains(res, 'Gagal', status_code=200)
        self.assertFalse(
            CalendarConnection.all_tenants.filter(user=self.staff_user).exists())


@override_settings(GOOGLE_CALENDAR_CLIENT_ID='cal-client-id',
                   GOOGLE_CALENDAR_CLIENT_SECRET='cal-secret')
class TokenLifecycleTest(BaseCalendarTestCase):
    def test_valid_token_used_without_refresh(self):
        conn = self._connection()
        with unittest.mock.patch('apps.calendar_sync.providers.requests.post') as m_post:
            token = get_valid_access_token(conn)
        m_post.assert_not_called()
        self.assertEqual(token, 'at-old')

    def test_expired_token_triggers_refresh_and_persists(self):
        conn = self._connection()
        conn.token_expires_at = timezone.now() - timedelta(seconds=1)
        conn.save()
        resp = unittest.mock.Mock(status_code=200)
        resp.json.return_value = {
            'access_token': 'at-refreshed', 'expires_in': 3600, 'email': 'x@y.z',
        }
        with unittest.mock.patch('apps.calendar_sync.providers.requests.post',
                                 return_value=resp) as m_post:
            token = get_valid_access_token(conn)
        self.assertEqual(token, 'at-refreshed')
        m_post.assert_called_once()
        self.assertEqual(m_post.call_args.kwargs['data']['grant_type'], 'refresh_token')
        conn.refresh_from_db()
        self.assertEqual(decrypt_secret(conn.access_token_encrypted), 'at-refreshed')
        # Google refresh responses omit refresh_token -> stored one preserved.
        self.assertEqual(decrypt_secret(conn.refresh_token_encrypted), 'rt-old')

    def test_revoked_refresh_token_marks_connection_revoked(self):
        conn = self._connection()
        conn.token_expires_at = timezone.now() - timedelta(seconds=1)
        conn.save()
        resp = unittest.mock.Mock(status_code=400)
        resp.json.return_value = {'error': 'invalid_grant'}
        with unittest.mock.patch('apps.calendar_sync.providers.requests.post',
                                 return_value=resp):
            with self.assertRaises(TokenExchangeError):
                get_valid_access_token(conn)
        conn.refresh_from_db()
        self.assertEqual(conn.status, CalendarConnection.STATUS_REVOKED)

    def test_connection_without_refresh_token_marks_error(self):
        conn = self._connection()
        conn.refresh_token_encrypted = ''
        conn.token_expires_at = timezone.now() - timedelta(seconds=1)
        conn.save()
        with self.assertRaises(TokenExchangeError):
            get_valid_access_token(conn)
        conn.refresh_from_db()
        self.assertEqual(conn.status, CalendarConnection.STATUS_ERROR)


@override_settings(GOOGLE_CALENDAR_CLIENT_ID='cal-client-id',
                   GOOGLE_CALENDAR_CLIENT_SECRET='cal-secret')
class SyncCronTest(BaseCalendarTestCase):
    def _list_events_ok(self, events):
        resp = unittest.mock.Mock(status_code=200)
        resp.json.return_value = _google_provider_response(events)
        return unittest.mock.patch('apps.calendar_sync.providers.requests.get',
                                   return_value=resp)

    def test_sync_ingests_events_idempotently(self):
        conn = self._connection()
        with self._list_events_ok([_g_event(), _g_event(id_='evt-2', summary='Rapat Wali')]):
            result = sync_connection(conn)
        self.assertTrue(result['ok'])
        self.assertEqual(result['created'], 2)
        with tenant_context(self.foundation.id):
            self.assertEqual(ExternalCalendarEvent.objects.filter(connection=conn).count(), 2)

        # Second run with the same events: no duplicates, rows updated in place.
        with self._list_events_ok([_g_event(), _g_event(id_='evt-2', summary='Rapat Wali')]):
            result2 = sync_connection(conn)
        self.assertEqual(result2['created'], 0)
        self.assertEqual(result2['updated'], 2)
        with tenant_context(self.foundation.id):
            self.assertEqual(ExternalCalendarEvent.objects.filter(connection=conn).count(), 2)

        conn.refresh_from_db()
        self.assertEqual(conn.status, CalendarConnection.STATUS_CONNECTED)
        self.assertIsNotNone(conn.last_synced_at)

    def test_sync_normalizes_fields(self):
        conn = self._connection()
        with self._list_events_ok([
            _g_event(hangout='https://meet.google.com/abc-defg'),
            _g_event(id_='evt-3', summary='Libur', status='cancelled'),
        ]):
            sync_connection(conn)
        with tenant_context(self.foundation.id):
            meeting = ExternalCalendarEvent.objects.get(connection=conn, provider_event_id='evt-1')
            self.assertEqual(meeting.meeting_url, 'https://meet.google.com/abc-defg')
            cancelled = ExternalCalendarEvent.objects.get(connection=conn, provider_event_id='evt-3')
            self.assertTrue(cancelled.is_cancelled)
            self.assertFalse(meeting.is_cancelled)

    def test_sync_failure_isolates_connections_and_marks_error(self):
        conn_ok = self._connection()
        # A second connection for a DIFFERENT user (unique constraint is
        # per user+provider) hits the patched 401 instead.
        conn_bad = self._connection(user=self.other_user, foundation_id=self.other_foundation.id)
        resp = unittest.mock.Mock(status_code=401)
        resp.json.return_value = {'error': 'unauthorized'}
        with self._list_events_ok([_g_event()]):
            result_ok = sync_connection(conn_ok)
        with unittest.mock.patch('apps.calendar_sync.providers.requests.get',
                                 return_value=resp):
            result_bad = sync_connection(conn_bad)
        self.assertTrue(result_ok['ok'])
        self.assertFalse(result_bad['ok'])
        conn_bad.refresh_from_db()
        self.assertEqual(conn_bad.status, CalendarConnection.STATUS_ERROR)
        self.assertIn('HTTP 401', conn_bad.last_error)
        # The failed connection's error never touched the healthy one.
        conn_ok.refresh_from_db()
        self.assertEqual(conn_ok.status, CalendarConnection.STATUS_CONNECTED)

    def test_command_runs_and_writes_jobrun(self):
        conn = self._connection()
        conn.token_expires_at = timezone.now() + timedelta(hours=1)
        conn.save()
        with self._list_events_ok([_g_event()]):
            from django.core.management import call_command
            call_command('sync_calendars')
        with tenant_context(self.foundation.id):
            self.assertEqual(ExternalCalendarEvent.objects.filter(connection=conn).count(), 1)
        job = JobRun.objects.filter(job_name='sync_calendars').latest('id')
        self.assertEqual(job.status, 'SUCCESS')
        self.assertEqual(job.items_processed, 1)

    def test_command_only_touches_connected(self):
        conn = self._connection(status=CalendarConnection.STATUS_REVOKED)
        with unittest.mock.patch('apps.calendar_sync.providers.requests.get') as m_get:
            from django.core.management import call_command
            call_command('sync_calendars')
        m_get.assert_not_called()
        with tenant_context(self.foundation.id):
            self.assertEqual(ExternalCalendarEvent.objects.filter(connection=conn).count(), 0)


class ReadApiTest(BaseCalendarTestCase):
    def _seed_event(self, conn, **overrides):
        defaults = dict(
            provider_event_id='evt-x', title='Ujian', start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
        )
        defaults.update(overrides)
        return ExternalCalendarEvent.all_tenants.create(
            foundation_id=conn.foundation_id, connection=conn, **defaults)

    def test_connection_list_and_delete(self):
        conn = self._connection()
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/calendar/connection/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['results'][0]['provider'], 'google')
        self.assertNotIn(b'access_token', res.render().content)  # never leak tokens

        res = self.client.delete('/api/v1/auth/calendar/connection/?provider=google')
        self.assertEqual(res.status_code, 204)
        conn.refresh_from_db()
        self.assertIsNotNone(conn.deleted_at)
        with tenant_context(self.foundation.id):
            self.assertEqual(ExternalCalendarEvent.objects.filter(connection=conn).count(), 0)

    def test_delete_unknown_provider_400(self):
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.delete('/api/v1/auth/calendar/connection/?provider=outlook')
        self.assertEqual(res.status_code, 400)

    def test_events_scoped_to_caller(self):
        mine = self._connection()
        theirs = self._connection(user=self.other_user, foundation_id=self.other_foundation.id)
        self._seed_event(mine, provider_event_id='mine-1')
        self._seed_event(theirs, provider_event_id='theirs-1')

        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/calendar/events/')
        self.assertEqual(res.status_code, 200)
        ids = [r['provider_event_id'] for r in res.data['results']]
        self.assertIn('mine-1', ids)
        self.assertNotIn('theirs-1', ids)

    def test_events_date_filtering(self):
        conn = self._connection()
        inside = timezone.now() + timedelta(days=2)
        outside = timezone.now() + timedelta(days=60)
        self._seed_event(conn, provider_event_id='in-range', start_at=inside,
                         end_at=inside + timedelta(hours=1))
        self._seed_event(conn, provider_event_id='out-range', start_at=outside,
                         end_at=outside + timedelta(hours=1))
        self.client.force_authenticate(user=self.staff_user)
        from_dt = (timezone.now() + timedelta(days=1)).date()
        to_dt = (timezone.now() + timedelta(days=3)).date()
        res = self.client.get(
            f'/api/v1/auth/calendar/events/?from={from_dt}&to={to_dt}')
        ids = [r['provider_event_id'] for r in res.data['results']]
        self.assertIn('in-range', ids)
        self.assertNotIn('out-range', ids)

    def test_events_require_auth(self):
        res = self.client.get('/api/v1/auth/calendar/events/')
        self.assertEqual(res.status_code, 401)


class CrossTenantTest(BaseCalendarTestCase):
    def test_events_never_cross_foundation(self):
        mine = self._connection()
        # Another user in ANOTHER foundation has a connection + events.
        theirs = self._connection(user=self.other_user, foundation_id=self.other_foundation.id)
        ExternalCalendarEvent.all_tenants.create(
            foundation_id=self.other_foundation.id, connection=theirs,
            provider_event_id='theirs-2', title='Rahasia',
            start_at=timezone.now(), end_at=timezone.now() + timedelta(hours=1),
        )
        set_current_foundation_id(self.foundation.id)
        self.client.force_authenticate(user=self.staff_user)
        res = self.client.get('/api/v1/auth/calendar/events/')
        ids = [r['provider_event_id'] for r in res.data['results']]
        self.assertNotIn('theirs-2', ids)
        # And the other foundation's user, in their context, cannot see mine.
        set_current_foundation_id(self.other_foundation.id)
        self.client.force_authenticate(user=self.other_user)
        res = self.client.get('/api/v1/auth/calendar/events/')
        ids = [r['provider_event_id'] for r in res.data['results']]
        self.assertNotIn('evt-x', ids)
        clear_current_foundation_id()


class ProviderNormalizationTest(BaseCalendarTestCase):
    def test_microsoft_graph_event_normalization(self):
        from apps.calendar_sync.providers import MicrosoftGraphCalendarProvider
        provider = MicrosoftGraphCalendarProvider('cid', 'csecret', 'http://r/')
        resp = unittest.mock.Mock(status_code=200)
        resp.json.return_value = {'value': [
            _ms_event(join_url='https://teams.microsoft.com/l/meetup-join/xyz'),
            _ms_event(id_='ms-evt-2', cancelled=True),
        ]}
        with unittest.mock.patch('apps.calendar_sync.providers.requests.get',
                                 return_value=resp):
            events = provider.list_events('token', 'primary',
                                          timezone.now(), timezone.now() + timedelta(days=1))
        self.assertEqual(events[0]['meeting_url'], 'https://teams.microsoft.com/l/meetup-join/xyz')
        self.assertEqual(events[0]['location'], 'Ruang Rapat')
        self.assertTrue(events[1]['is_cancelled'])
        self.assertTrue(timezone.is_aware(events[0]['start_at']))

    def test_google_all_day_event(self):
        from apps.calendar_sync.providers import GoogleCalendarProvider
        provider = GoogleCalendarProvider('cid', 'csecret', 'http://r/')
        resp = unittest.mock.Mock(status_code=200)
        resp.json.return_value = {'items': [{
            'id': 'allday-1', 'summary': 'Hari Guru', 'status': 'confirmed',
            'start': {'date': '2026-11-25'}, 'end': {'date': '2026-11-26'},
        }]}
        with unittest.mock.patch('apps.calendar_sync.providers.requests.get',
                                 return_value=resp):
            events = provider.list_events('token', 'primary',
                                          timezone.now(), timezone.now() + timedelta(days=90))
        self.assertTrue(events[0]['is_all_day'])
        self.assertTrue(timezone.is_aware(events[0]['start_at']))

    def test_provider_disabled_fails_loud(self):
        with override_settings(GOOGLE_CALENDAR_CLIENT_ID=''):
            with self.assertRaises(ProviderDisabledError):
                get_provider('google', 'http://r/')

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            get_provider('yahoo', 'http://r/')


class IngestTest(BaseCalendarTestCase):
    def test_ingest_skips_malformed_rows(self):
        conn = self._connection()
        result = ingest_events(conn, [
            {'id': '', 'start_at': timezone.now(), 'end_at': timezone.now()},
            {'id': 'ok-1', 'title': 'OK', 'start_at': timezone.now(),
             'end_at': timezone.now() + timedelta(hours=1)},
            {'id': 'no-start', 'title': 'Bad'},
        ])
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['skipped'], 2)

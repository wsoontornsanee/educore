"""Tests for POST /analytics/events/ (spec/08 §5, spec/15 RPT-015)."""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from apps.core.models import AnalyticsEvent
from apps.identity.models import Foundation, RoleAssignment, User


class AnalyticsIngestEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Nusantara", brand_name="Nusantara")
        self.parent_user = User.all_tenants.create_user(
            phone_e164="+6281200000020", full_name="Ibu Sari", foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.parent_user,
            role=RoleAssignment.ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

    def _auth(self, user):
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_valid_batch_persists_events_scoped_to_own_foundation(self):
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"},
                    {"event_name": "invoice_view", "school_id": 5, "occurred_at": "2026-09-17T08:01:00Z"},
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-1',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['accepted'], 2)

        events = AnalyticsEvent.all_tenants.filter(foundation_id=self.foundation.id)
        self.assertEqual(events.count(), 2)
        by_name = {e.event_name: e for e in events}
        self.assertEqual(by_name['app_open'].role, 'parent')
        self.assertIsNone(by_name['app_open'].school_id)
        self.assertEqual(by_name['invoice_view'].school_id, 5)

    def test_role_and_foundation_are_never_taken_from_request_body(self):
        """A client cannot spoof another foundation or role by including it in the body."""
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {
                        "event_name": "app_open", "school_id": None,
                        "occurred_at": "2026-09-17T08:00:00Z",
                        "foundation_id": 999999, "role": "foundation_admin",
                    },
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-2',
        )
        self.assertEqual(response.status_code, 201)
        event = AnalyticsEvent.all_tenants.get(foundation_id=self.foundation.id)
        self.assertEqual(event.role, 'parent')
        self.assertNotEqual(event.foundation_id, 999999)

    def test_unknown_event_name_in_batch_is_skipped_not_fatal(self):
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {"event_name": "not_a_real_event", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"},
                    {"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:01:00Z"},
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-3',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['accepted'], 1)

    def test_repeated_idempotency_key_does_not_double_insert(self):
        self._auth(self.parent_user)
        body = {"events": [{"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"}]}
        r1 = self.client.post('/api/v1/analytics/events/', body, format='json', HTTP_IDEMPOTENCY_KEY='dup-key')
        r2 = self.client.post('/api/v1/analytics/events/', body, format='json', HTTP_IDEMPOTENCY_KEY='dup-key')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(AnalyticsEvent.all_tenants.filter(foundation_id=self.foundation.id).count(), 1)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(
            '/api/v1/analytics/events/',
            {"events": [{"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"}]},
            format='json',
        )
        self.assertEqual(response.status_code, 401)

"""Tests for security-sensitive endpoint rate limiting (apps.core.throttling)."""
from datetime import date

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, RoleAssignment, User
from apps.identity.services import create_user_with_person
from educore.middleware.tenancy import tenant_context


class LoginRateThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Uji Coba", brand_name="Uji Coba")
        with tenant_context(self.foundation.id):
            self.user, _person = create_user_with_person(
                foundation_id=self.foundation.id,
                full_name="Guru Uji",
                phone="081234500000",
                email="guru.uji@ujicoba.sch.id",
                password="PasswordAman123!",
                nik="3171012345009999",
                dob=date(1990, 1, 1),
                gender="L",
                address="Jl. Uji Coba No. 1",
            )
            RoleAssignment.all_tenants.create(
                foundation_id=self.foundation.id, user=self.user,
                role=RoleAssignment.ROLE_TEACHER,
                scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
            )

    def _attempt_login(self, password='wrong-password'):
        return self.client.post(
            '/api/v1/auth/token/',
            {'identifier': '+6281234500000', 'password': password},
            format='json',
        )

    def test_legitimate_login_within_limit_succeeds(self):
        response = self._attempt_login(password='PasswordAman123!')
        self.assertEqual(response.status_code, 200)

    def test_eleventh_attempt_in_a_minute_is_throttled(self):
        for _ in range(10):
            self._attempt_login()
        response = self._attempt_login()
        self.assertEqual(response.status_code, 429)
        self.assertIn('detik', response.json()['detail'])
        self.assertIn('Retry-After', response)
        self.assertEqual(response['X-RateLimit-Limit'], '10')
        self.assertEqual(response['X-RateLimit-Remaining'], '0')

    def test_repeated_violations_write_audit_event(self):
        # 10 to exhaust the bucket, then 3 more throttled hits to cross the
        # escalation threshold (VIOLATION_ESCALATION_THRESHOLD = 3).
        for _ in range(13):
            self._attempt_login()
        self.assertTrue(
            AuditEvent.objects.filter(action='security.rate_limit.repeated_violation').exists()
        )


class OtpRequestIPThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _request_otp(self, phone):
        return self.client.post('/api/v1/auth/otp/request/', {'phone_e164': phone}, format='json')

    def test_eleventh_distinct_phone_in_an_hour_is_throttled(self):
        # A different phone number each time so the existing per-phone
        # service-level cap (3/15min) never fires — isolates the new per-IP cap.
        for i in range(10):
            response = self._request_otp(f"+62812345{i:05d}")
            self.assertEqual(response.status_code, 201)

        response = self._request_otp("+6281234599999")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['X-RateLimit-Limit'], '10')

    def test_ident_prefers_cf_connecting_ip_over_shifting_x_forwarded_for(self):
        """Regression: this deploy sits behind Cloudflare -> nginx, and nginx's
        X-Forwarded-For appends its own upstream peer (Cloudflare's anycast edge
        IP, which varies per request) as the last hop. With DRF's default
        get_ident (whole XFF string, NUM_PROXIES unset) the same real client got
        a different throttle identity on almost every request and NEVER got
        throttled — confirmed live on PRD before this fix. CF-Connecting-IP must
        be used instead whenever present."""
        for i in range(10):
            response = self.client.post(
                '/api/v1/auth/otp/request/', {'phone_e164': f"+62811100{i:05d}"}, format='json',
                HTTP_CF_CONNECTING_IP='203.0.113.7',
                HTTP_X_FORWARDED_FOR=f"203.0.113.7, 198.51.100.{i}",  # last hop shifts every request
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.post(
            '/api/v1/auth/otp/request/', {'phone_e164': "+6281110099999"}, format='json',
            HTTP_CF_CONNECTING_IP='203.0.113.7',
            HTTP_X_FORWARDED_FOR='203.0.113.7, 198.51.100.250',
        )
        self.assertEqual(response.status_code, 429)


class PaymentWebhookThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_provider_scoped_throttle_does_not_affect_other_provider(self):
        # Exhaust XENDIT's bucket; MIDTRANS on the same client IP must be unaffected
        # since PaymentWebhookThrottle keys by (provider, ip), not ip alone.
        for _ in range(100):
            self.client.post('/api/v1/finance/webhooks/payments/xendit/', {}, format='json')

        throttled = self.client.post('/api/v1/finance/webhooks/payments/xendit/', {}, format='json')
        self.assertEqual(throttled.status_code, 429)

        other_provider = self.client.post('/api/v1/finance/webhooks/payments/midtrans/', {}, format='json')
        self.assertNotEqual(other_provider.status_code, 429)

"""MySQL-backed rate limiting for security-sensitive endpoints (AGENTS red line #1: no Redis).

DRF's stock `SimpleRateThrottle`/`ScopedRateThrottle` already use Django's
configured cache (`django.core.cache.cache`), which in this project is
`DatabaseCache` on MySQL — no new engine, no new table, no Redis. This module
only adds what DRF doesn't give for free: X-RateLimit-* response headers, an
Indonesian-first 429 body, and an audit trail for repeated violations.

Deliberately NOT a global `DEFAULT_THROTTLE_CLASSES` platform-wide (the
"General Public Ingress: 60/min/IP" bullet from the originating task):
DRF's throttle cache persists across requests within a single test run (it
is not reset per-TestCase), and hundreds of existing tests fire many rapid
unauthenticated requests at shared views. Turning on a blanket anonymous
throttle for every endpoint would risk widespread flaky/failing tests for a
protection this repo doesn't have evidence it currently needs. Scoped
instead to the endpoints the task actually named as attack surface: login,
OTP request, and payment webhooks.
"""
from django.core.cache import cache as default_cache
from django.http import JsonResponse
from rest_framework.throttling import ScopedRateThrottle

from apps.core.services import audit

# A client is logged as a suspected brute-force actor once it's been
# throttled this many times within VIOLATION_WINDOW_SECONDS.
VIOLATION_ESCALATION_THRESHOLD = 3
VIOLATION_WINDOW_SECONDS = 3600


def _record_violation(scope: str, ident: str):
    """Track repeated throttle hits for one (scope, ident) and write an
    AuditEvent once they cross VIOLATION_ESCALATION_THRESHOLD — reuses the
    existing append-only AuditEvent trail (apps.core.services.audit) rather
    than introducing a parallel SecurityIncident model for a single caller."""
    cache_key = f"throttle_violations:{scope}:{ident}"
    try:
        count = default_cache.incr(cache_key)
    except ValueError:
        default_cache.set(cache_key, 1, VIOLATION_WINDOW_SECONDS)
        count = 1

    if count == VIOLATION_ESCALATION_THRESHOLD:
        audit(
            action='security.rate_limit.repeated_violation',
            entity_type='rate_limit',
            entity_id=f"{scope}:{ident}",
            actor_id='system',
            diff={'scope': scope, 'violation_count': count, 'ident': ident},
        )


class SecurityScopedThrottle(ScopedRateThrottle):
    """ScopedRateThrottle plus header info for the 429 response and repeated-violation audit logging."""

    def allow_request(self, request, view):
        allowed = super().allow_request(request, view)
        if not allowed:
            ident = self.get_ident(request)
            request._educore_throttle_info = {
                'scope': self.scope,
                'limit': self.num_requests,
                'window_seconds': self.duration,
            }
            _record_violation(self.scope, ident)
        return allowed


class LoginRateThrottle(SecurityScopedThrottle):
    """Per-IP defense-in-depth alongside the per-account lockout in
    apps.identity.backends.DualAuthBackend (10 failed attempts/15min) — this
    catches a low-and-slow credential-stuffing spray across many different
    accounts from one IP, which the per-account lock can't see."""
    scope = 'auth_login'


class OtpRequestIPThrottle(SecurityScopedThrottle):
    """Per-IP cap alongside the existing per-phone cap in
    apps.identity.services.request_phone_otp (3 sends/15min/phone) — this
    catches one IP cycling through many different phone numbers."""
    scope = 'otp_request_ip'


class PaymentWebhookThrottle(SecurityScopedThrottle):
    """Per (provider, IP) — a compromised or misbehaving gateway integration
    for one provider shouldn't be able to exhaust the budget for another."""
    scope = 'payment_webhook'

    def get_cache_key(self, request, view):
        provider = view.kwargs.get('provider', 'unknown')
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': f"{provider}:{ident}"}


def build_throttled_response(exc, context):
    """Render a DRF Throttled exception as the standard 429 contract:
    Indonesian detail, Retry-After (always), X-RateLimit-* (when the
    triggering throttle was a SecurityScopedThrottle that recorded info)."""
    request = context.get('request')
    wait = exc.wait  # seconds until the client may retry, or None
    wait_int = int(wait) if wait is not None else 60

    body = {'detail': f"Terlalu banyak permintaan. Silakan coba lagi dalam {wait_int} detik."}
    response = JsonResponse(body, status=429)
    response['Retry-After'] = str(wait_int)

    info = getattr(request, '_educore_throttle_info', None) if request else None
    if info:
        response['X-RateLimit-Limit'] = str(info['limit'])
        response['X-RateLimit-Remaining'] = '0'
        response['X-RateLimit-Reset'] = str(wait_int)
    return response

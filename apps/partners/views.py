"""Base DRF view for the partner API surface.

PartnerAPIView ties together (spec/18):
- PartnerHMACAuthentication (§2)
- per-key rate limiting (§8)
- RFC 9457 problem+json error rendering (§5)
- Idempotency-Key replay on mutating endpoints (§4), reusing
  core.IdempotencyRecord with a 409 IDEMPOTENCY_MISMATCH code.

Partner requests carry no session user, so tenancy is derived from the key's
foundation (set on the thread-local by the authentication class) — the Layer-2
TenantManager then fail-closes every query to that foundation.
"""
from django.db import connection
from rest_framework import status as drf_status
from rest_framework.views import APIView

from apps.core.models import IdempotencyRecord
from apps.core.idempotency import compute_request_hash
from apps.partners.errors import (PartnerAPIError, problem_response,
                                  problem_from_exception, parse_json_body)
from apps.partners.rate_limit import check_rate_limit

IDEMPOTENCY_HEADER = 'Idempotency-Key'
IDEMPOTENCY_RETENTION_DAYS = 7  # §4: keys retained 7 days


class PartnerAPIView(APIView):
    """All /api/v1/partner/ endpoints inherit from this."""

    authentication_classes = []  # PartnerHMACAuthentication runs manually in dispatch
    permission_classes = []
    pagination_class = None

    def _render(self, request, response):
        # Attach Retry-After for rate limit errors.
        return response

    def dispatch(self, request, *args, **kwargs):
        from apps.partners.authentication import PartnerHMACAuthentication
        try:
            # 1. Authenticate (signature + freshness + key state).
            authenticator = PartnerHMACAuthentication()
            try:
                result = authenticator.authenticate(request)
            except PartnerAPIError as exc:
                return problem_response(request, exc)
            if result is None:
                return problem_response(request, PartnerAPIError(401, 'SIGNATURE_INVALID', 'Authentication failed.'))
            request.user, request.auth = result

            # 2. Rate limit per key (§8).
            try:
                check_rate_limit(request.partner_key.key_id)
            except PartnerAPIError as exc:
                resp = problem_response(request, exc)
                if hasattr(exc, 'retry_after'):
                    resp['Retry-After'] = str(exc.retry_after)
                return resp

            # 3. Idempotency replay for mutating methods (§4).
            self._idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
            self._idempotent_replay = None
            if self._idempotency_key and request.method in ('POST', 'PUT', 'PATCH'):
                self._idempotency_key = self._idempotency_key[:128]
                body = self._raw_body(request)
                self._request_hash = compute_request_hash(request.method, request.path, body)
                replay = self._lookup_idempotency(request, request.partner_key.key_id)
                if replay is not None:
                    return replay

            # 4. Dispatch the handler.
            response = super().dispatch(request, *args, **kwargs)
        except PartnerAPIError as exc:
            response = problem_response(request, exc)
        except Exception as exc:  # unexpected — render opaque problem+json
            response = problem_from_exception(request, exc)

        # 5. Store idempotent response (2xx/3xx only), like core.IdempotentViewMixin.
        if getattr(self, '_idempotency_key', None) and request.method in ('POST', 'PUT', 'PATCH'):
            self._store_idempotency(request, response)
        return response

    def _raw_body(self, request) -> bytes:
        body = getattr(request, '_body', None)
        if body is None:
            try:
                body = request.body
            except Exception:
                body = b''
        return body or b''

    def get_json_body(self, request) -> dict:
        return parse_json_body(request)

    # -- Idempotency ----------------------------------------------------

    def _idempotency_endpoint(self, request) -> str:
        return f"partner:{request.partner_key.key_id}:{request.path}"[:255]

    def _lookup_idempotency(self, request, key_id):
        cutoff = timezone_now_minus_days(IDEMPOTENCY_RETENTION_DAYS)
        record = IdempotencyRecord.objects.filter(
            key=f"{key_id}:{self._idempotency_key}", endpoint=self._idempotency_endpoint(request),
            created_at__gte=cutoff,
        ).first()
        if record is None:
            return None
        if record.request_hash != self._request_hash:
            return problem_response(request, PartnerAPIError(
                409, 'IDEMPOTENCY_MISMATCH',
                'Idempotency-Key was already used with a different payload.'))
        from django.http import HttpResponse
        resp = HttpResponse(record.response_body, status=record.response_status, content_type='application/json')
        resp['X-Idempotent-Replay'] = 'true'
        return resp

    def _store_idempotency(self, request, response):
        if not (200 <= response.status_code < 400):
            return
        if hasattr(response, 'render') and callable(getattr(response, 'render', None)):
            try:
                response.render()
            except Exception:
                return
        body = ''
        if hasattr(response, 'content'):
            body = response.content.decode('utf-8', errors='replace')
        elif hasattr(response, 'data'):
            import json
            body = json.dumps(response.data)
        try:
            IdempotencyRecord.objects.update_or_create(
                key=f"{request.partner_key.key_id}:{self._idempotency_key}",
                defaults={
                    'endpoint': self._idempotency_endpoint(request),
                    'request_hash': getattr(self, '_request_hash', ''),
                    'response_body': body,
                    'response_status': response.status_code,
                },
            )
        except Exception:
            pass


def timezone_now_minus_days(days: int):
    from django.utils import timezone
    from datetime import timedelta
    return timezone.now() - timedelta(days=days)


def close_old_connections():
    connection.close_if_unusable_or_obsolete()

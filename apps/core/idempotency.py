"""DRF Idempotency support backed by apps.core.models.IdempotencyRecord (ARC-010, ARC-014)."""
import hashlib
import json
import logging
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from apps.core.models import IdempotencyRecord

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = 'HTTP_IDEMPOTENCY_KEY'


def compute_request_hash(method: str, path: str, body: bytes) -> str:
    """Compute deterministic SHA-256 hash of HTTP method, path, and raw body."""
    hasher = hashlib.sha256()
    hasher.update(method.upper().encode('utf-8'))
    hasher.update(b':')
    hasher.update(path.encode('utf-8'))
    hasher.update(b':')
    hasher.update(body or b'')
    return hasher.hexdigest()


class IdempotentViewMixin:
    """DRF View / ViewSet Mixin that enforces idempotent writes via Idempotency-Key.

    Protocol:
    1. If `Idempotency-Key` header is missing, request proceeds normally.
    2. If key exists in `IdempotencyRecord`:
       - If `request_hash` matches: replay stored response with status and body, adding `X-Cache: HIT-Idempotent`.
       - If `request_hash` differs: return 409 Conflict (`IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`).
    3. If key is new:
       - Execute view handler.
       - If response is successful (200 <= status < 400), save `IdempotencyRecord`.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._idempotency_key = request.META.get(IDEMPOTENCY_HEADER)
        self._cached_idempotent_response = None

        if not self._idempotency_key:
            return

        # Sanitize key length to fit model CharField(max_length=128)
        if len(self._idempotency_key) > 128:
            self._idempotency_key = self._idempotency_key[:128]

        body = getattr(request, '_body', None)
        if body is None:
            try:
                body = request.body
            except Exception:
                body = b''

        self._request_hash = compute_request_hash(request.method, request.path, body)

        try:
            record = IdempotencyRecord.objects.filter(key=self._idempotency_key).first()
            if record:
                if record.request_hash == self._request_hash:
                    # Match: prepare replayed response
                    content_type = 'application/json' if record.response_body.startswith('{') or record.response_body.startswith('[') else 'text/plain; charset=utf-8'
                    cached_resp = HttpResponse(
                        content=record.response_body,
                        status=record.response_status,
                        content_type=content_type,
                    )
                    cached_resp['X-Cache'] = 'HIT-Idempotent'
                    self._cached_idempotent_response = cached_resp
                else:
                    # Mismatch: key reuse with differing body/endpoint
                    conflict_resp = Response(
                        {
                            "error": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
                            "detail": "Kunci idempotensi telah digunakan untuk muatan data yang berbeda.",
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                    self._cached_idempotent_response = conflict_resp
        except Exception as exc:
            logger.warning("Failed to check IdempotencyRecord for key %s: %s", self._idempotency_key, exc)

    def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            self.initial(request, *args, **kwargs)

            if getattr(self, '_cached_idempotent_response', None) is not None:
                response = self._cached_idempotent_response
            else:
                if request.method.lower() in self.http_method_names:
                    handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
                else:
                    handler = self.http_method_not_allowed

                response = handler(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)

        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response

    def finalize_response(self, request, response, *args, **kwargs):
        if getattr(self, '_cached_idempotent_response', None) is not None:
            return self._cached_idempotent_response

        final_response = super().finalize_response(request, response, *args, **kwargs)

        key = getattr(self, '_idempotency_key', None)
        if key and 200 <= final_response.status_code < 400:
            try:
                if hasattr(final_response, 'render') and callable(final_response.render):
                    final_response.render()

                if hasattr(final_response, 'content'):
                    body_str = final_response.content.decode('utf-8', errors='replace')
                elif hasattr(final_response, 'data'):
                    body_str = json.dumps(final_response.data)
                else:
                    body_str = ''

                IdempotencyRecord.objects.update_or_create(
                    key=key,
                    defaults={
                        'endpoint': request.path[:255],
                        'request_hash': getattr(self, '_request_hash', ''),
                        'response_body': body_str,
                        'response_status': final_response.status_code,
                    }
                )
            except Exception as exc:
                logger.warning("Failed to save IdempotencyRecord for key %s: %s", key, exc)

        return final_response

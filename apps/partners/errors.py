"""RFC 9457 (problem+json) error handling for the partner API surface (§5).

Scoped deliberately to apps.partners views only for non-throttle errors —
the rest of the platform keeps its existing error shape. `Throttled` is the
one exception type this global handler treats platform-wide (see
`_drf_problem_handler` below and apps/core/throttling.py).
"""
import json
import logging

from django.http import JsonResponse
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class PartnerAPIError(APIException):
    """A partner-surface error that renders as an RFC 9457 problem+json body.

    Subclasses DRF's APIException so errors raised inside DRF's own pipeline
    (e.g. ScopeRequiredMixin.initial) route through handle_exception instead
    of crashing as uncaught. Both render paths — PartnerAPIView.dispatch's
    problem_response and DRF's exception handler — end at the same
    problem+json body via _drf_problem_handler below.

    `code` is the stable SCREAMING_SNAKE_CASE string integrators branch on —
    never `detail`, which is free text (§5).
    """

    def __init__(self, status: int, code: str, detail: str, title: str = None):
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or code.replace('_', ' ').capitalize()
        self.status_code = status  # APIException contract
        super().__init__(f"{code}: {detail}")


def _drf_problem_handler(exc, context):
    """Global DRF EXCEPTION_HANDLER.

    Partner views (apps.partners): render RFC 9457 problem+json (§5).
    `Throttled` (any app): render the platform's standard 429 contract —
    Indonesian detail + Retry-After + X-RateLimit-* (apps.core.throttling).
    Every other app/exception: delegate to DRF's own default handler
    UNCHANGED — this handler is a strict no-op for anything else.
    """
    request = context.get('request')
    view = context.get('view')
    if isinstance(exc, PartnerAPIError):
        return problem_response(request, exc)

    from rest_framework.exceptions import Throttled
    if isinstance(exc, Throttled):
        from apps.core.throttling import build_throttled_response
        return build_throttled_response(exc, context)

    from rest_framework.views import exception_handler as drf_default
    is_partner_view = view is not None and (
        view.__class__.__module__.startswith('apps.partners'))
    if not is_partner_view:
        return drf_default(exc, context)

    from django.http import Http404
    from rest_framework.exceptions import PermissionDenied
    if isinstance(exc, Http404):
        return problem_response(request, PartnerAPIError(
            404, 'NOT_FOUND', 'Resource not found.'))
    if isinstance(exc, PermissionDenied):
        return problem_response(request, PartnerAPIError(
            403, 'SCOPE_DENIED', 'Permission denied.'))
    resp = drf_default(exc, context)
    if resp is not None:
        code = getattr(exc, 'default_code', 'ERROR').upper()
        detail = getattr(exc, 'detail', None)
        return problem_response(request, PartnerAPIError(
            resp.status_code, code,
            detail if isinstance(detail, str) else str(detail)))
    return None


def problem_response(request, error: PartnerAPIError) -> JsonResponse:
    """Render a PartnerAPIError as application/problem+json."""
    body = {
        'type': f"/errors/{error.code.lower().replace('_', '-')}",
        'title': error.title,
        'status': error.status,
        'detail': error.detail,
        'instance': request.path,
        'code': error.code,
    }
    return JsonResponse(body, status=error.status, content_type='application/problem+json')


def problem_from_exception(request, exc: Exception) -> JsonResponse:
    """Fallback rendering for unexpected exceptions (logged, opaque detail)."""
    from django.http import Http404
    if isinstance(exc, PartnerAPIError):
        return problem_response(request, exc)
    if isinstance(exc, Http404):
        return problem_response(request, PartnerAPIError(404, 'NOT_FOUND', 'Resource not found.'))
    logger.exception("Unhandled partner API error on %s", request.path)
    return problem_response(request, PartnerAPIError(500, 'INTERNAL_ERROR', 'Internal server error.'))


def json_body_bytes(request) -> bytes:
    """Raw request body — safe to call even after DRF has buffered it."""
    body = getattr(request, '_body', None)
    if body is None:
        try:
            body = request.body
        except Exception:
            body = b''
    return body or b''


def parse_json_body(request) -> dict:
    from rest_framework.exceptions import ParseError
    body = json_body_bytes(request)
    if not body:
        return {}
    try:
        import json as _json
        parsed = _json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise PartnerAPIError(400, 'VALIDATION_ERROR', 'Request body is not valid JSON.')
    if not isinstance(parsed, dict):
        raise PartnerAPIError(400, 'VALIDATION_ERROR', 'Request body must be a JSON object.')
    return parsed

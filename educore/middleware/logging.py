"""Structured access-log middleware: assigns a request_id, times the request,
and logs one JSON line per response via apps.core.logging.JSONFormatter."""
import logging
import threading
import time
import uuid

logger = logging.getLogger('educore.request')

_thread_locals = threading.local()


def get_current_request_id():
    """Retrieve the current thread-local request_id (mirrors tenancy.py/audit.py)."""
    return getattr(_thread_locals, 'request_id', None)


def _set_current_request_id(request_id):
    _thread_locals.request_id = request_id


def _clear_current_request_id():
    if hasattr(_thread_locals, 'request_id'):
        del _thread_locals.request_id


class RequestLoggingMiddleware:
    """Emits one structured access-log entry per request (timestamp/level/logger/
    message/foundation_id/user_id/request_id/path/method/status_code/duration_ms).

    request_id is also published via get_current_request_id() — the same
    thread-local pattern as get_current_foundation_id()/get_current_actor() —
    so apps.core.services.audit() and business-logic loggers deep in the call
    stack can tag their own entries with it without request being threaded
    through every call.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex
        request.request_id = request_id
        _set_current_request_id(request_id)
        started = time.monotonic()
        status_code = None

        try:
            response = self.get_response(request)
            status_code = response.status_code
            response['X-Request-ID'] = request_id
            return response
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            user = getattr(request, 'user', None)
            user_id = getattr(user, 'id', None) if user and getattr(user, 'is_authenticated', False) else None

            logger.info(
                '%s %s %s',
                request.method, request.path, status_code,
                extra={
                    'request_id': request_id,
                    # TenancyMiddleware runs deeper in the chain and stamps
                    # this onto the request object (not just its own
                    # thread-local) before this middleware resumes.
                    'foundation_id': getattr(request, 'foundation_id', None),
                    'user_id': user_id,
                    'path': request.path,
                    'method': request.method,
                    'status_code': status_code,
                    'duration_ms': duration_ms,
                },
            )
            _clear_current_request_id()

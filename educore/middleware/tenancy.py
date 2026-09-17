"""Multi-tenancy middleware and thread-local state management.

Implements ARC-002:
Default queryset filters by current request's foundation, read from thread-local
set by TenancyMiddleware.
"""
import threading
from contextlib import contextmanager

_thread_locals = threading.local()

def get_current_foundation_id():
    """Retrieve the current thread-local foundation ID."""
    return getattr(_thread_locals, 'foundation_id', None)

def set_current_foundation_id(foundation_id):
    """Set the current thread-local foundation ID."""
    _thread_locals.foundation_id = foundation_id

def clear_current_foundation_id():
    """Clear thread-local foundation ID."""
    if hasattr(_thread_locals, 'foundation_id'):
        del _thread_locals.foundation_id

@contextmanager
def tenant_context(foundation_id):
    """Context manager to execute code under a specific foundation context.
    
    Essential for management commands, background tasks, and tests.
    """
    previous_foundation_id = get_current_foundation_id()
    set_current_foundation_id(foundation_id)
    try:
        yield
    finally:
        if previous_foundation_id is not None:
            set_current_foundation_id(previous_foundation_id)
        else:
            clear_current_foundation_id()

class TenancyMiddleware:
    """Middleware resolving the active foundation context for incoming HTTP requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        foundation_id = None

        # 1. From authenticated user or test client _force_auth_user
        user = getattr(request, '_force_auth_user', getattr(request, 'user', None))
        if user and getattr(user, 'is_authenticated', False):
            foundation_id = getattr(user, 'foundation_id', None)

        # 2. From header (for service-to-service or privileged API access).
        #    When the user is already authenticated, the header is ignored —
        #    the authenticated user's foundation is authoritative. A mismatch
        #    between header and user foundation is validated by the DRF auth
        #    layer (EduCoreJWTAuthentication) for JWT requests.
        if not foundation_id:
            header_fid = request.headers.get('X-Foundation-ID')
            if header_fid and header_fid.isdigit():
                foundation_id = int(header_fid)

        set_current_foundation_id(foundation_id)
        request.foundation_id = foundation_id

        try:
            response = self.get_response(request)
        finally:
            clear_current_foundation_id()

        return response

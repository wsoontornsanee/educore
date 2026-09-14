"""Audit middleware to capture actor context for explicit audit logging."""
import threading

_audit_locals = threading.local()

def get_current_actor():
    """Return the current actor (user representation or system)."""
    return getattr(_audit_locals, 'actor', None)

def get_current_ip():
    """Return the client IP address of the current request."""
    return getattr(_audit_locals, 'ip_address', None)

def set_current_audit_context(actor=None, ip_address=None):
    """Set the current audit context."""
    _audit_locals.actor = actor
    _audit_locals.ip_address = ip_address

def clear_current_audit_context():
    """Clear thread-local audit context."""
    if hasattr(_audit_locals, 'actor'):
        del _audit_locals.actor
    if hasattr(_audit_locals, 'ip_address'):
        del _audit_locals.ip_address

class AuditMiddleware:
    """Middleware extracting request actor and client IP for audit trail context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip_address = self._get_client_ip(request)
        actor = getattr(request, 'user', None) if getattr(request, 'user', None) and request.user.is_authenticated else None

        set_current_audit_context(actor=actor, ip_address=ip_address)

        try:
            response = self.get_response(request)
        finally:
            clear_current_audit_context()

        return response

    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')

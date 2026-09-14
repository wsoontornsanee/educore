"""Timezone middleware activating institution timezone per request."""
import zoneinfo
from django.utils import timezone

class TimezoneMiddleware:
    """Middleware activating the local Indonesian timezone for current user/school."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_tz = None
        user = getattr(request, 'user', None)

        if user and user.is_authenticated:
            # School or user timezone attribute if available
            user_tz = getattr(user, 'timezone', None)
            if not user_tz and hasattr(user, 'school'):
                user_tz = getattr(user.school, 'timezone', None)

        if not user_tz:
            user_tz = 'Asia/Jakarta'  # Default to Western Indonesia Time (WIB)

        try:
            timezone.activate(zoneinfo.ZoneInfo(user_tz))
        except Exception:
            timezone.activate(zoneinfo.ZoneInfo('Asia/Jakarta'))

        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()

        return response

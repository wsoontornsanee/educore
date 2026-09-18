"""Template context processors for apps.identity."""
from educore.middleware.tenancy import get_current_foundation_id
from .nav import get_nav_for_user


def console_nav(request):
    """Injects `nav_groups` into every template context for an authenticated request.

    Anonymous requests (the login page, marketing pages) get an empty list —
    harmless, since base_console.html is only extended by authenticated
    console pages, and base.html (which those other pages use) never
    references nav_groups at all.
    """
    if not request.user.is_authenticated:
        return {'nav_groups': []}
    foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
    if not foundation_id:
        return {'nav_groups': []}
    return {'nav_groups': get_nav_for_user(request.user, foundation_id)}

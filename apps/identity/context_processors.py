"""Template context processors for apps.identity."""
from django.utils.functional import SimpleLazyObject

from educore.middleware.tenancy import get_current_foundation_id
from .inbox import get_inbox_count
from .nav import get_nav_for_user

INBOX_BADGE_CAP = 99


def _inbox_badge(user, foundation_id):
    """Nav badge text for the task inbox: '' when nothing is pending."""
    count = get_inbox_count(user, foundation_id)
    return f"{INBOX_BADGE_CAP}+" if count > INBOX_BADGE_CAP else (str(count) if count else '')


def console_nav(request):
    """Injects `nav_groups` and `inbox_badge` into every template context for
    an authenticated request.

    Anonymous requests (the login page, marketing pages) get an empty list —
    harmless, since base_console.html is only extended by authenticated
    console pages, and base.html (which those other pages use) never
    references nav_groups at all.

    `inbox_badge` is lazy: its count queries run only if a template actually
    reads it (console_nav.html), not on every render of every page.
    """
    if not request.user.is_authenticated:
        return {'nav_groups': [], 'inbox_badge': ''}
    foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
    if not foundation_id:
        return {'nav_groups': [], 'inbox_badge': ''}
    return {
        'nav_groups': get_nav_for_user(request.user, foundation_id),
        'inbox_badge': SimpleLazyObject(lambda: _inbox_badge(request.user, foundation_id)),
    }

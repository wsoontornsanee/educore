"""Per-role post-login landing resolution for the web console.

Replaces the binary "grades.read or holding page" branch PR #187 shipped
as a stopgap (apps/identity/web_views.py's _default_landing_url) with an
explicit per-role map. The no-RoleAssignment-at-all fallback is left
exactly as PR #187 built it (imported, not reimplemented) — see
docs/superpowers/specs/2026-09-18-web-console-nav-and-landing-design.md.
"""
from .models import RoleAssignment

ROLE_LANDING_URLS = {
    RoleAssignment.ROLE_FOUNDATION_ADMIN: '/web/home/overview/',
    RoleAssignment.ROLE_SCHOOL_ADMIN: '/web/home/today/',
    RoleAssignment.ROLE_TEACHER: '/web/home/agenda/',
    RoleAssignment.ROLE_FINANCE_OFFICER: '/web/home/billing/',
    RoleAssignment.ROLE_COUNSELLOR: '/web/home/',
    RoleAssignment.ROLE_CANTEEN_OPERATOR: '/web/home/',
    RoleAssignment.ROLE_CLINIC_OFFICER: '/web/home/',
}
# dict insertion order IS the priority order (per the design spec's own
# ROLE_LANDING_URLS ordering): foundation_admin outranks school_admin
# outranks teacher outranks finance_officer outranks the three roles that
# share the generic /web/home/ landing.
ROLE_LANDING_PRIORITY = list(ROLE_LANDING_URLS)


def resolve_post_login_redirect(user, foundation_id):
    """Highest-priority role `user` holds (via RoleAssignment, any scope) ->
    that role's landing URL. A user with zero RoleAssignment rows falls
    back to the pre-existing grades.read-based _default_landing_url logic
    (imported lazily to avoid a circular import with web_views)."""
    if not foundation_id:
        from .web_views import NO_CONSOLE_PAGE_URL
        return NO_CONSOLE_PAGE_URL

    held_roles = set(
        RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user=user, deleted_at__isnull=True,
        ).values_list('role', flat=True)
    )
    for role in ROLE_LANDING_PRIORITY:
        if role in held_roles:
            return ROLE_LANDING_URLS[role]

    from .web_views import _default_landing_url
    return _default_landing_url(user, foundation_id)

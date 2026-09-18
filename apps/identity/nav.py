"""Permission-gated global navigation table for the web console.

Every item declares the RBAC permission key that gates it — the SAME
predicate apps.identity.permissions.HasRequiredPermission uses to gate the
click (apps.identity.rbac.has_permission). This is deliberate: a hardcoded
per-role item list drifts from what a role can actually reach (see the
Notion investigation that found Finance Officer/Canteen Operator/Clinic
Officer lacked grades.read despite the source mockup's role lists implying
otherwise). permission=None means "always shown to any authenticated
staff user" (used only for the task inbox item: every source it lists
applies its own permission/ownership check, see apps.identity.inbox).

Only 'inbox', the four Operasional items (attendance, permission_slips, canteen,
exam), the three Keuangan items and the four Administrasi items have real
destinations (console-inbox, attendance-gate-console-page,
permission-slip-console-page, canteen-console-page, exam-mode-console-page,
finance-console-*, admin-staff/partners/settings/audit). Every other
item still routes to the 'console:coming_soon' placeholder in this table (kept
so a future task can flip one item's url_name the moment
its real page ships), but get_nav_for_user hides every coming_soon item
from the rendered nav — reported as a bug (2026-09-19): a user with full
permissions saw the whole menu, clicked into modules that only ever showed
"Modul ini belum tersedia", and read that as a broken/no-access page rather
than an unbuilt one. The corresponding dev work is tracked in Notion
instead of being exposed as a dead menu item. See
docs/superpowers/specs/2026-09-18-web-console-nav-and-landing-design.md.

An item may also declare requires_staff_profile=True: the Operasional pages
gate on more than the RBAC permission key (apps.identity.console_access
.StaffConsoleMixin also requires a linked Staff row — ROLE_PARENT holds
attendance.read / grades.read / wallet.topup.read, and a guardian must never
reach a school-side console). Without this check the
nav would show an item that then 404s on click instead of just not showing
it — reported as a real bug (2026-09-18) against a Teacher-role account
with no Staff profile.

requires_foundation_admin=True is the same idea for pages whose backing API
gates on is_foundation_admin rather than a permission key (the partner-key
console): school_admin holds school_config.write too, but the page would only
redirect them home, so the nav must not show it.
"""
from django.utils.translation import gettext_lazy as _

from .models import RoleAssignment, Staff
from .rbac import get_user_permissions, is_foundation_admin, SCOPE_SCHOOL

COMING_SOON_URL_NAME = "console:coming_soon"

NAV_GROUPS = [
    {"label": _("Beranda"), "items": [
        {"id": "inbox", "label": _("Kotak tugas"), "permission": None, "url_name": "console-inbox"},
    ]},
    {"label": _("Akademik"), "items": [
        {"id": "roster", "label": _("Siswa & kelas"), "permission": "student_records.read", "url_name": "academic-class-list-page", "requires_staff_profile": True},
        {"id": "schedule", "label": _("Jadwal"), "permission": "student_records.read", "url_name": "academic-timetable-page", "requires_staff_profile": True},
        {"id": "grading", "label": _("Antrean penilaian"), "permission": "grades.read", "url_name": "academic-grading-queue-page", "requires_staff_profile": True},
        {"id": "reports", "label": _("Rapor"), "permission": "grades.read", "url_name": "academic-report-card-list-page", "requires_staff_profile": True},
    ]},
    {"label": _("Operasional"), "items": [
        {"id": "attendance", "label": _("Kehadiran & gerbang"), "permission": "attendance.read", "url_name": "attendance-gate-console-page", "requires_staff_profile": True},
        {"id": "permission_slips", "label": _("Izin digital"), "permission": "grades.read", "url_name": "permission-slip-console-page", "requires_staff_profile": True},
        {"id": "canteen", "label": _("Kantin & dompet"), "permission": "wallet.topup.read", "url_name": "canteen-console-page", "requires_staff_profile": True},
        {"id": "exam", "label": _("Mode ujian"), "permission": "grades.read", "url_name": "exam-mode-console-page", "requires_staff_profile": True},
    ]},
    {"label": _("Keuangan"), "items": [
        {"id": "billing", "label": _("Tagihan & pembayaran"), "permission": "finance.invoice.read", "url_name": "finance-console-billing", "requires_staff_profile": True},
        {"id": "recon", "label": _("Rekonsiliasi"), "permission": "finance.payment.read", "url_name": "finance-console-reconciliation", "requires_staff_profile": True},
        {"id": "ar", "label": _("Piutang & keringanan"), "permission": "finance.invoice.read", "url_name": "finance-console-receivables", "requires_staff_profile": True},
    ]},
    {"label": _("Administrasi"), "items": [
        {"id": "staff", "label": _("Staf & jabatan"), "permission": "school_config.read", "url_name": "admin-staff"},
        {"id": "partners", "label": _("Mitra & kunci API"), "permission": "school_config.write", "url_name": "admin-partners", "requires_foundation_admin": True},
        {"id": "settings", "label": _("Pengaturan sekolah"), "permission": "school_config.write", "url_name": "admin-settings"},
        {"id": "audit", "label": _("Jejak audit"), "permission": "audit_log.read", "url_name": "admin-audit"},
    ]},
]


def _cumulative_permissions(user, foundation_id):
    """The user's cumulative permission set across every scope they hold:
    foundation scope, unioned with every school they're individually
    assigned to.

    Queries the assigned-schools list ONCE and calls
    apps.identity.rbac.get_user_permissions ONCE per scope (foundation +
    each assigned school) — a small, fixed number of queries regardless of
    how many nav items exist, unlike a naive per-item has_permission() call
    (which re-ran the assigned-schools query and the role-assignment query
    for every single nav item — see the nav-computation N+1 perf fix in the
    2026-09-18 web-console-nav-and-landing final review)."""
    permissions = set(get_user_permissions(user, foundation_id, school_id=None))
    assigned_schools = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    for school_id in assigned_schools:
        permissions |= get_user_permissions(user, foundation_id, school_id=school_id)
    return permissions


def has_staff_profile(user, foundation_id):
    """Explicit foundation_id filter via .all_tenants, not the thread-local-
    dependent .objects — matches the same explicit-scoping convention used
    throughout apps.identity.web_views (this runs inside a global context
    processor, so it should not silently depend on TenancyMiddleware having
    already set the thread-local for this exact request)."""
    return Staff.all_tenants.filter(
        user=user, foundation_id=foundation_id, deleted_at__isnull=True,
    ).exists()


def get_nav_for_user(user, foundation_id):
    """NAV_GROUPS filtered to items `user` can actually reach and use: the
    item must not be a coming_soon placeholder, they must hold its RBAC
    permission, and — for the handful of items that declare
    requires_staff_profile — have a linked Staff row too.

    Groups whose every item was filtered out are omitted entirely (a group
    label alone, with no clickable items, is dead chrome for that user).
    """
    permissions = _cumulative_permissions(user, foundation_id)
    needs_staff_check = any(
        item.get("requires_staff_profile") for group in NAV_GROUPS for item in group["items"]
    )
    has_staff = has_staff_profile(user, foundation_id) if needs_staff_check else None
    needs_admin_check = any(
        item.get("requires_foundation_admin") for group in NAV_GROUPS for item in group["items"]
    )
    is_admin = is_foundation_admin(user, foundation_id) if needs_admin_check else None

    result = []
    for group in NAV_GROUPS:
        visible_items = [
            {"id": item["id"], "label": item["label"], "url_name": item["url_name"]}
            for item in group["items"]
            if item["url_name"] != COMING_SOON_URL_NAME
            and (item["permission"] is None or item["permission"] in permissions)
            and (not item.get("requires_staff_profile") or has_staff)
            and (not item.get("requires_foundation_admin") or is_admin)
        ]
        if visible_items:
            result.append({"label": group["label"], "items": visible_items})
    return result

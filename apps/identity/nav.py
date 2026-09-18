"""Permission-gated global navigation table for the web console.

Every item declares the RBAC permission key that gates it — the SAME
predicate apps.identity.permissions.HasRequiredPermission uses to gate the
click (apps.identity.rbac.has_permission). This is deliberate: a hardcoded
per-role item list drifts from what a role can actually reach (see the
Notion investigation that found Finance Officer/Canteen Operator/Clinic
Officer lacked grades.read despite the source mockup's role lists implying
otherwise). permission=None means "always shown to any authenticated
staff user" (used only for the placeholder task inbox item, which has no
real backing feature yet either).

Only 'permission_slips' has a real destination (permission-slip-console-page).
Every other item routes to the 'console:coming_soon' placeholder — see
docs/superpowers/specs/2026-09-18-web-console-nav-and-landing-design.md.
"""
from django.utils.translation import gettext_lazy as _

from .models import RoleAssignment
from .rbac import get_user_permissions, SCOPE_SCHOOL

NAV_GROUPS = [
    {"label": _("Beranda"), "items": [
        {"id": "inbox", "label": _("Kotak tugas"), "permission": None, "url_name": "console:coming_soon"},
    ]},
    {"label": _("Akademik"), "items": [
        {"id": "roster", "label": _("Siswa & kelas"), "permission": "student_records.read", "url_name": "console:coming_soon"},
        {"id": "schedule", "label": _("Jadwal"), "permission": "student_records.read", "url_name": "console:coming_soon"},
        {"id": "grading", "label": _("Antrean penilaian"), "permission": "grades.read", "url_name": "console:coming_soon"},
        {"id": "reports", "label": _("Rapor"), "permission": "grades.read", "url_name": "console:coming_soon"},
    ]},
    {"label": _("Operasional"), "items": [
        {"id": "attendance", "label": _("Kehadiran & gerbang"), "permission": "attendance.read", "url_name": "console:coming_soon"},
        {"id": "permission_slips", "label": _("Izin digital"), "permission": "grades.read", "url_name": "permission-slip-console-page"},
        {"id": "canteen", "label": _("Kantin & dompet"), "permission": "wallet.topup.read", "url_name": "console:coming_soon"},
        {"id": "exam", "label": _("Mode ujian"), "permission": "grades.read", "url_name": "console:coming_soon"},
    ]},
    {"label": _("Keuangan"), "items": [
        {"id": "billing", "label": _("Tagihan & pembayaran"), "permission": "finance.invoice.read", "url_name": "console:coming_soon"},
        {"id": "recon", "label": _("Rekonsiliasi"), "permission": "finance.payment.read", "url_name": "console:coming_soon"},
        {"id": "ar", "label": _("Piutang & keringanan"), "permission": "finance.invoice.read", "url_name": "console:coming_soon"},
    ]},
    {"label": _("Administrasi"), "items": [
        {"id": "staff", "label": _("Staf & jabatan"), "permission": "school_config.read", "url_name": "console:coming_soon"},
        {"id": "partners", "label": _("Mitra & kunci API"), "permission": "school_config.write", "url_name": "console:coming_soon"},
        {"id": "settings", "label": _("Pengaturan sekolah"), "permission": "school_config.write", "url_name": "console:coming_soon"},
        {"id": "audit", "label": _("Jejak audit"), "permission": "audit_log.read", "url_name": "console:coming_soon"},
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


def get_nav_for_user(user, foundation_id):
    """NAV_GROUPS filtered to items `user` actually holds the permission for.

    Groups whose every item was filtered out are omitted entirely (a group
    label alone, with no clickable items, is dead chrome for that user).
    """
    permissions = _cumulative_permissions(user, foundation_id)
    result = []
    for group in NAV_GROUPS:
        visible_items = [
            {"id": item["id"], "label": item["label"], "url_name": item["url_name"]}
            for item in group["items"]
            if item["permission"] is None or item["permission"] in permissions
        ]
        if visible_items:
            result.append({"label": group["label"], "items": visible_items})
    return result

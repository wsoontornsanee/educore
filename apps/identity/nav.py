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
from .models import RoleAssignment
from .rbac import has_permission, SCOPE_SCHOOL

NAV_GROUPS = [
    {"label": "Beranda", "items": [
        {"id": "inbox", "label": "Kotak tugas", "permission": None, "url_name": "console:coming_soon"},
    ]},
    {"label": "Akademik", "items": [
        {"id": "roster", "label": "Siswa & kelas", "permission": "student_records.read", "url_name": "console:coming_soon"},
        {"id": "schedule", "label": "Jadwal", "permission": "student_records.read", "url_name": "console:coming_soon"},
        {"id": "grading", "label": "Antrean penilaian", "permission": "grades.read", "url_name": "console:coming_soon"},
        {"id": "reports", "label": "Rapor", "permission": "grades.read", "url_name": "console:coming_soon"},
    ]},
    {"label": "Operasional", "items": [
        {"id": "attendance", "label": "Kehadiran & gerbang", "permission": "attendance.read", "url_name": "console:coming_soon"},
        {"id": "permission_slips", "label": "Izin digital", "permission": "grades.read", "url_name": "permission-slip-console-page"},
        {"id": "canteen", "label": "Kantin & dompet", "permission": "wallet.topup.read", "url_name": "console:coming_soon"},
        {"id": "exam", "label": "Mode ujian", "permission": "grades.read", "url_name": "console:coming_soon"},
    ]},
    {"label": "Keuangan", "items": [
        {"id": "billing", "label": "Tagihan & pembayaran", "permission": "finance.invoice.read", "url_name": "console:coming_soon"},
        {"id": "recon", "label": "Rekonsiliasi", "permission": "finance.payment.read", "url_name": "console:coming_soon"},
        {"id": "ar", "label": "Piutang & keringanan", "permission": "finance.invoice.read", "url_name": "console:coming_soon"},
    ]},
    {"label": "Administrasi", "items": [
        {"id": "staff", "label": "Staf & jabatan", "permission": "school_config.read", "url_name": "console:coming_soon"},
        {"id": "partners", "label": "Mitra & kunci API", "permission": "school_config.write", "url_name": "console:coming_soon"},
        {"id": "settings", "label": "Pengaturan sekolah", "permission": "school_config.write", "url_name": "console:coming_soon"},
        {"id": "audit", "label": "Jejak audit", "permission": "audit_log.read", "url_name": "console:coming_soon"},
    ]},
]


def _has_permission_in_any_scope(user, permission_key, foundation_id):
    """has_permission checked at foundation scope, then across every school
    the user is individually assigned to. Mirrors the identical fallback
    apps.identity.permissions.HasRequiredPermission and
    apps.identity.web_views._default_landing_url already use — has_permission
    itself only checks ONE scope per call (school_id=None means "foundation
    scope only", per apps.identity.rbac.get_user_role_assignments' own
    docstring), so a SCHOOL-scoped role (how most staff — teachers, school
    admins — are actually assigned) would otherwise never match here."""
    if has_permission(user, permission_key, foundation_id, school_id=None):
        return True
    assigned_schools = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id, user=user, scope_type=SCOPE_SCHOOL, deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    return any(
        has_permission(user, permission_key, foundation_id, school_id=school_id)
        for school_id in assigned_schools
    )


def get_nav_for_user(user, foundation_id):
    """NAV_GROUPS filtered to items `user` actually holds the permission for.

    Groups whose every item was filtered out are omitted entirely (a group
    label alone, with no clickable items, is dead chrome for that user).
    """
    result = []
    for group in NAV_GROUPS:
        visible_items = [
            {"id": item["id"], "label": item["label"], "url_name": item["url_name"]}
            for item in group["items"]
            if item["permission"] is None
            or _has_permission_in_any_scope(user, item["permission"], foundation_id)
        ]
        if visible_items:
            result.append({"label": group["label"], "items": visible_items})
    return result

# Web Console: Global Navigation Menu & Role-Aware Post-Login Landing — Design

**Notion task:** [Web Console: Build Global Navigation Menu & Role-Aware Post-Login Landing](https://app.notion.com/p/3df347a66594814a8143e3a6d9635755)

**Source design:** `https://claude.ai/artifact/FEgyBdDFLK5DLbiYVZjxN2` (desktop/tablet/phone, 4 preview roles: chair/admin/teacher/finance).

## Problem

`frontend/templates/base.html` has no navigation. Every feature page (Permission Slip Console, and everything else) exists only as a standalone page reached by a direct URL, never linked from anywhere.

**Update — superseded by PR #187 (merged during this design's brainstorming, before this spec was committed):** the original hard-403 severity issue (`finance_officer`/`canteen_operator`/`clinic_officer` hitting a raw 403 on login) is already fixed on `main`. `apps/identity/web_views.py` now has `_default_landing_url(user, foundation_id)`, checking `has_permission(user, 'grades.read', ...)` with a foundation-then-assigned-school fallback, routing anyone without `grades.read` to a new minimal holding page (`WebConsoleHomeView` at `/web/home/`, `pages/console_home.html`: "Belum ada halaman konsol web khusus untuk peran Anda saat ini."). This is a real, already-shipped, permission-based (not role-list) redirect — not a placeholder to throw away. This design **extends** it: replaces the current binary "grades.read or holding page" branching with a real per-role landing map, and turns the holding page into the generic landing for roles with no dedicated dashboard (scope decision 2, below) rather than building either from scratch.

## Investigation findings (binding on scope)

- The web console today has exactly **one** real feature page: Permission Slip Console (`apps/academic/web_urls.py`, gated by `grades.read`). Everything else the source mockup's nav lists (roster, schedule, grading, reports, attendance, canteen, exam, billing, recon, ar, staff, partners, settings, audit) has **no backing Django view or URL** in this repo.
- `frontend/templates/components/{gradebook_grid,grading_queue,exam_proctor_console,pos_kiosk,teacher_agenda,exam_lockdown,parent_nutrition_dashboard}.html` exist on disk but are never `render()`-ed by any view (confirmed via repo-wide grep) — dead, unwired component templates from an earlier design import. This design does not wire them up; that is separate, larger follow-on work.
- `GET /api/v1/me` (`apps.identity.views.CurrentUserView`) returns `roles` (raw `RoleAssignment` rows) and `entitlements` (per-foundation module on/off flags: academic/attendance/finance/wallet/campus_life/payroll/analytics/hardware). No page in this repo currently gates on a module entitlement flag — only on RBAC permission keys via `HasRequiredPermission`. Consuming entitlement flags for nav visibility today would be speculative (nothing to test it against); this design defers that to when a page-level entitlement gate actually exists, and gates nav visibility by the same mechanism that gates the click: `has_permission(user, permission_key, foundation_id)`.
- Real RBAC roles (`apps.identity.models.RoleAssignment`): `foundation_admin`, `school_admin`, `finance_officer`, `teacher`, `counsellor`, `canteen_operator`, `clinic_officer`, `parent`. The source mockup only defines rich landing content for 4 of these (chair→foundation_admin, admin→school_admin, teacher→teacher, finance→finance_officer). `parent` is out of scope (mobile app only, existing precedent per `memory/01_PROJECT.md`).

## Scope decisions (confirmed with user)

1. **Nav shell + honest placeholders.** Build the full nav chrome from the design (sidebar/drawer/tab-bar, permission-gated groups) with every item from the mockup. Items with no real page yet route to a shared "module not available" placeholder instead of 404/403. No new console feature pages are built in this slice beyond the placeholder and the landing pages themselves.
2. **Missing-role landings.** `counsellor`, `canteen_operator`, `clinic_officer` get a generic landing (greeting + their own permission-gated nav items listed as links) — no invented per-role dashboard content, since the source design never specified any.
3. **i18n: one real mechanism.** The nav's language control visually matches the mockup (flag + language code in the header) but submits to the existing `set_language` Django view — no client-side dictionary, no `localStorage`. All new copy goes through `{% translate %}`/the `.po` catalog, using the mockup's own ID source + EN translation pairs verbatim (it ships a complete 1:1 dictionary — reuse it, don't re-translate).
4. **Design-tool chrome excluded.** The mockup's "Pratinjau peran" role-switcher bar and the desktop/tablet/phone view-switcher are design-preview tooling only; a real user never switches their own role or forces a viewport. Not built.

## Architecture

### Nav gating: permission-based, not role-name lists

Each nav item declares a `required_permission` (an existing RBAC key, e.g. `'grades.read'`) instead of a hardcoded role array. Visibility is computed the same way `HasRequiredPermission` gates the click: `has_permission(request.user, item.permission, foundation_id)`. This is what the severity investigation's own per-role permission table proved is the real predicate — a role name is not.

`apps/identity/nav.py` (new):
```python
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

def get_nav_for_user(user, foundation_id):
    """Filter NAV_GROUPS down to items the user's real permissions grant. Groups with zero visible items are omitted."""
```

`{"id": "permission_slips", ...}` is the **one item with a real destination** (`permission-slip-console-page`); every other `url_name` points at the new `console:coming_soon` placeholder route. `permission=None` items (only `inbox`) are always shown to any authenticated staff user (no real inbox exists yet either, but it's a harmless always-visible placeholder matching the mockup's cross-role item).

Known, accepted consequence of switching from the mockup's hardcoded per-role item lists to a real permission check: an item the mockup restricted to one role (e.g. "Mitra & kunci API" shown only to `chair` in the mockup) may now also show for another role that happens to share the same permission (`school_admin` also has `school_config.write`). This is correct, not a regression — the mockup's role lists were illustrative wireframe data, not a security boundary; the permission check is.

### Placeholder page

New `apps/core/web_views.py::ComingSoonView` (a `TemplateView`, session-auth required via Django's own `LoginRequiredMixin` — no RBAC permission needed since reaching it at all already implies the user held the item's permission) rendering `frontend/templates/pages/coming_soon.html`: "Modul ini belum tersedia" / "This module isn't available yet", with a link back to the user's own landing page. Registered at `console:coming_soon` under a new `apps/core/web_urls.py`, mounted at `/web/coming-soon/`.

### Nav shell templates

- `frontend/templates/components/console_nav.html` — the sidebar/drawer nav markup (desktop rail, tablet auto-collapsed rail, phone drawer + bottom tab bar), consuming `nav_groups` from context. Faithful to the mockup's layout/spacing/colors (already matches `spec/17-design-system-and-ui.md` — same red/mono/Jakarta Sans tokens as the rest of the app).
- `frontend/templates/base_console.html` — new base template for authenticated console pages, extending `base.html`, replacing its plain header with the full nav shell (sidebar + top bar with breadcrumb/page title/scope/notification/language). `permission-slip-console-page` and the new landing/coming-soon pages extend this instead of `base.html` directly. `base.html` itself is untouched (marketing/status pages, and the plain login page, keep their existing header).
- Language control in the top bar: a button styled like the mockup's flag+code control that is really a compact `set_language` form (same mechanism `base.html` already uses, just restyled to match the console header).

### Role-aware landing

Correction against `apps.identity.rbac.ROLE_PERMISSIONS` (verified, not assumed): `counsellor` already holds `grades.read`, so it is not one of the three roles PR #187 found hitting the holding page (`finance_officer`, `canteen_operator`, `clinic_officer` are). `counsellor` is still included in the generic landing group here for a different reason: the source mockup defines no custom dashboard content for it either, same as the other two — this is a design-content decision, independent of PR #187's permission-based routing fix.

`apps/identity/landing.py` (new), replacing `_default_landing_url`'s binary branch with an explicit per-role map — the existing foundation-then-assigned-school `has_permission` resolution stays as the final fallback for the one case a role map can't cover (a user with no `RoleAssignment` row at all):
```python
ROLE_LANDING_URLS = {
    RoleAssignment.ROLE_FOUNDATION_ADMIN: '/web/home/overview/',
    RoleAssignment.ROLE_SCHOOL_ADMIN: '/web/home/today/',
    RoleAssignment.ROLE_TEACHER: '/web/home/agenda/',
    RoleAssignment.ROLE_FINANCE_OFFICER: '/web/home/billing/',
    RoleAssignment.ROLE_COUNSELLOR: NO_CONSOLE_PAGE_URL,
    RoleAssignment.ROLE_CANTEEN_OPERATOR: NO_CONSOLE_PAGE_URL,
    RoleAssignment.ROLE_CLINIC_OFFICER: NO_CONSOLE_PAGE_URL,
}
ROLE_LANDING_PRIORITY = list(ROLE_LANDING_URLS)  # foundation_admin > school_admin > ... , dict insertion order

def resolve_post_login_redirect(user, foundation_id) -> str:
    """Highest-priority role the user holds (by RoleAssignment, any scope) ->
    that role's landing URL. A user with no RoleAssignment row at all falls
    back to _default_landing_url's existing has_permission('grades.read')
    check, unchanged from today's behavior for that edge case."""
```

`WebLoginView`'s three `next_url = request.POST.get('next') or DEFAULT_REDIRECT_URL` / `request.GET.get('next') or DEFAULT_REDIRECT_URL` call sites (`apps/identity/web_views.py:34,80,143`) become `... or resolve_post_login_redirect(request.user)` — an explicit `?next=` (a real deep link) still always wins, unchanged from today; the change is only what happens when there's no explicit `next`.

Landing pages (`apps/identity/web_views.py`, new views, one per real-data role + one generic):
- `foundation_admin`/`school_admin`/`teacher`/`finance_officer`: real page rendering the mockup's greeting/stat-cards/queue/sidebar layout, but populated only from data that's cheap and real to query today (e.g. attendance rate via existing `apps.attendance` queries, AR aging via existing `apps.finance` aggregates) — anywhere the mockup's mock numbers don't have a real, already-built query behind them (e.g. "penghapusan piutang tak tertagih" queue items, "2 kandidat" hiring pipeline), that section is omitted rather than faked. Each role's landing keeps the mockup's own explanatory "Aturan pendaratan" callout, translated to describe what's actually shown.
- `counsellor`/`canteen_operator`/`clinic_officer`: route to `NO_CONSOLE_PAGE_URL` (`/web/home/`) — the existing `WebConsoleHomeView`/`console_home.html`, extended (not replaced) to additionally render the user's own `get_nav_for_user` list as a simple link grid below the existing greeting, instead of just the static "no page for your role yet" message.

Exact per-role query list and template breakdown is a planning-phase concern (writing-plans), not fixed further here, to avoid the design doc going stale against what's actually queryable when implementation starts.

### PermissionSlipWebAccessMixin's staff-profile-404 edge case

`PermissionSlipConsolePageView` already 404s a permission-holding user with no linked `Staff` profile (`"Akun ini tidak terhubung ke profil staf."`). `resolve_post_login_redirect` does not special-case this — it routes by role the same way `_default_landing_url` already does today, so this specific edge case's behavior is unchanged (still a 404 for that one pre-existing gap, not something this task introduces or is asked to fix).

### Call sites

`_default_landing_url`'s three existing call sites (`apps/identity/web_views.py:69,164,225` — `WebLoginView.get`, `WebLoginView.post`, `WebSSOLoginView.post`) all already call it only when there's no explicit `next` (an explicit `?next=`/`next` POST field, a real deep link, always wins today — unchanged). This task's only change to those call sites is what `_default_landing_url` returns in the no-`next` case: today it's binary (permission-slip console or the holding page); after this task it consults `ROLE_LANDING_URLS` first.

## Testing

- `apps/identity/tests/test_nav.py`: `get_nav_for_user` returns only permission-held items, groups with zero visible items are omitted, `permission=None` items always show for an authenticated user.
- `apps/identity/tests/test_landing.py`: `resolve_post_login_redirect` per-role priority order (a user with both `teacher` and `foundation_admin` assignments lands on the foundation_admin URL), no-role-assignment fallback (unchanged from today's `has_permission('grades.read')` check).
- `apps/identity/tests/test_web_views.py` (extend existing, which already covers PR #187's holding-page behavior): `finance_officer`/`canteen_operator`/`clinic_officer` now land on the extended `/web/home/` with their nav links rendered, not just the static message.
- `apps/core/tests/test_coming_soon.py`: anonymous denied, authenticated 200.
- Template smoke test: nav renders for each of the 7 non-parent roles without error, contains only permission-held item labels.

## Non-goals (logged as follow-up Notion items during planning)

- Building real feature pages behind any placeholder item (roster/schedule/grading/etc.) — this task only ships the nav shell and honest placeholders.
- Wiring `frontend/templates/components/{gradebook_grid,...}` dead templates to real views.
- Consuming `GET /api/v1/me` entitlement flags for nav gating (no page-level entitlement gate exists yet to make this meaningful).
- A "real dashboard" beyond what's cheaply queryable from existing data today.

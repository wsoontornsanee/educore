# Web Console Nav + Role-Aware Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every authenticated web-console user a persistent, permission-gated navigation menu and a real per-role post-login landing page, replacing the binary "permission-slip-console or holding page" redirect PR #187 shipped as a stopgap.

**Architecture:** `apps.identity.nav.get_nav_for_user` filters a static nav-group table by real RBAC permission checks (`has_permission`, the same predicate `HasRequiredPermission` uses to gate the click) and renders it via a new `components/console_nav.html` included from a new `base_console.html` (extends the existing `base.html`, replacing its header for console pages only). `apps.identity.landing.resolve_post_login_redirect` extends PR #187's `_default_landing_url` with an explicit per-role landing URL map instead of its current binary branch. Items with no real backing page (everything except Permission Slip Console) route to a shared placeholder instead of 404/403.

**Tech Stack:** Django 5.x templates (no build step, matches repo convention), existing DRF `HasRequiredPermission`/`has_permission`, existing `set_language` Django i18n view, SQLite for fast tests (`EDUCORE_USE_SQLITE=1`).

## Global Constraints

- Nav item visibility is computed via `apps.identity.rbac.has_permission(user, permission_key, foundation_id, school_id=None)` — never a hardcoded role-name list (design spec, Architecture § Nav gating).
- `RoleAssignment`, `TenantManager`, `TenancyMiddleware`, and PR #187's `_default_landing_url` foundation-then-assigned-school fallback logic are extended, not replaced or duplicated — the fallback for a user with zero `RoleAssignment` rows stays exactly as PR #187 shipped it.
- Only ONE nav item gets a real destination this slice: `permission_slips` → `permission-slip-console-page`. Every other item routes to the new `console:coming_soon` placeholder — do not build any other real feature page.
- `counsellor`/`canteen_operator`/`clinic_officer` land on the existing `/web/home/` (`WebConsoleHomeView`/`console_home.html`), extended with a nav-links grid — do not build a separate template for them.
- All new user-facing copy goes through `{% translate %}`/`{% blocktranslate %}` with the ID string as the msgid and an EN `.po` entry added by hand (never run `makemessages` — repo precedent: it fuzzy-clobbers unrelated already-translated strings, see `memory/01_PROJECT.md`'s i18n regression entries). Exact ID/EN pairs are given in each task from the source design's own translation dictionary.
- The mockup's role-switcher preview bar and desktop/tablet/phone view-switcher are NOT built (design-tool-only chrome).
- No entitlement-flag (`GET /api/v1/me`) consumption for nav gating in this slice (design spec non-goal — no page-level entitlement gate exists yet to make it meaningful).
- Landing-page stats use only the three simple, single-model aggregate queries defined in Task 5 (`get_landing_stats`) — do not add new cross-module aggregate services in this slice.
- Tests via `EDUCORE_USE_SQLITE=1 python3 manage.py test ...` (no `.venv` in shared worktrees; use `python3`). Every task ends with a commit, Conventional Commits format.
- No hard deletes anywhere (AGENTS.md red line #2) — not applicable to this feature (no deletion of any kind here), noted only for completeness.

---

### Task 1: `apps.identity.nav` — permission-gated nav table

**Files:**
- Create: `apps/identity/nav.py`
- Test: `apps/identity/tests/test_nav.py`

**Interfaces:**
- Consumes: `apps.identity.rbac.has_permission(user, permission_key, foundation_id, school_id=None) -> bool` (existing).
- Produces: `apps.identity.nav.NAV_GROUPS` (list of `{"label": str, "items": [{"id": str, "label": str, "permission": str | None, "url_name": str}]}`), `apps.identity.nav.get_nav_for_user(user, foundation_id) -> list[dict]` returning `[{"label": str, "items": [{"id": str, "label": str, "url_name": str}]}]` with items the user lacks permission for removed and empty groups omitted.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_nav.py
from django.test import TestCase
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.nav import get_nav_for_user


class GetNavForUserTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.teacher = User.objects.create(
            phone_e164='+6281300000001', full_name='Teacher One', foundation_id=self.foundation.id,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.canteen = User.objects.create(
            phone_e164='+6281300000002', full_name='Canteen One', foundation_id=self.foundation.id,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.canteen, role=RoleAssignment.ROLE_CANTEEN_OPERATOR,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )

    def test_teacher_sees_grades_and_attendance_items_not_finance(self):
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('grading', item_ids)
        self.assertIn('attendance', item_ids)
        self.assertNotIn('recon', item_ids)
        self.assertNotIn('partners', item_ids)

    def test_permissionless_item_always_shown_to_authenticated_user(self):
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('inbox', item_ids)

    def test_empty_groups_are_omitted(self):
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        for group in nav:
            self.assertGreater(len(group['items']), 0)
        group_labels = {g['label'] for g in nav}
        self.assertNotIn('Administrasi', group_labels)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_nav -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.identity.nav'`

- [ ] **Step 3: Implement**

`apps/identity/nav.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_nav -v 2`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/nav.py apps/identity/tests/test_nav.py
git commit -m "feat(identity): add permission-gated nav table for the web console"
```

---

### Task 2: Placeholder "coming soon" page (`apps.core`)

**Files:**
- Modify: `apps/core/views.py` (append)
- Modify: `apps/core/urls.py` — check current content first; if it only holds API routes, create a new `apps/core/web_urls.py` instead (see Step 3) and wire it in `educore/urls.py`.
- Create: `frontend/templates/pages/coming_soon.html`
- Test: `apps/core/tests/test_coming_soon.py`

**Interfaces:**
- Produces: `apps.core.views.ComingSoonView` (session-auth `View`, not DRF `APIView` — this page needs no RBAC permission check, since reaching any nav item that points here already implied the user held that item's permission; it only needs to reject anonymous users). URL name `console:coming_soon` at `/web/coming-soon/`.

- [ ] **Step 1: Write the failing test**

```python
# apps/core/tests/test_coming_soon.py
from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, User


class ComingSoonViewTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.user = User.objects.create(phone_e164='+6281300000003', full_name='U', foundation_id=self.foundation.id)
        self.user.set_password('pw12345')
        self.user.save()

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/web/login/', response.url)

    def test_authenticated_gets_200(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'belum tersedia')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.core.tests.test_coming_soon -v 2`
Expected: FAIL — `NoReverseMatch: 'console' is not a registered namespace`

- [ ] **Step 3: Implement**

Append to `apps/core/views.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ComingSoonView(LoginRequiredMixin, TemplateView):
    """GET /web/coming-soon/ — shared placeholder for nav items with no real page yet.

    LoginRequiredMixin redirects anonymous users to settings.LOGIN_URL
    (already configured to /web/login/ for the rest of the web console).
    No RBAC permission check here: every nav item pointing at this view
    already gated the LINK by the item's own permission (apps.identity.nav);
    this view's only job is to not be reachable while logged out.
    """
    template_name = 'pages/coming_soon.html'
```

Create `apps/core/web_urls.py`:

```python
"""Web (session-auth) URL routes for apps.core — mounted under /web/."""
from django.urls import path

from .views import ComingSoonView

app_name = 'console'

urlpatterns = [
    path('coming-soon/', ComingSoonView.as_view(), name='coming_soon'),
]
```

Create `frontend/templates/pages/coming_soon.html`:

```html
{% extends "base_console.html" %}
{% load i18n %}

{% block title %}{% translate "Modul belum tersedia" %}{% endblock %}

{% block content %}
<div style="max-width: 560px; margin: 64px auto; text-align: center;">
  <h1 class="font-display text-h2" style="margin-bottom: 12px;">
    {% translate "Modul ini belum tersedia" %}
  </h1>
  <p class="text-body-sm text-ink-700">
    {% translate "Fitur ini sedang dalam pengembangan. Hubungi Tata Usaha (TU) sekolah jika Anda memerlukan akses ke fitur tertentu." %}
  </p>
</div>
{% endblock %}
```

In `educore/urls.py`, add (near the other `/web/` includes):

```python
    path('web/', include('apps.core.web_urls')),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.core.tests.test_coming_soon -v 2`
Expected: FAIL (this task's `coming_soon.html` extends `base_console.html`, which doesn't exist until Task 3). Per this plan's task ordering, do Task 3 immediately after this one before running the full suite — same ordering caveat as the Service Status Page plan's Task 9/10 split. If you need this task's own tests green in isolation first, temporarily change `coming_soon.html`'s `{% extends %}` to `"base.html"` and switch it to `"base_console.html"` in Task 3's own step instead.

Run again after Task 3: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.core.tests.test_coming_soon -v 2`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/core/views.py apps/core/web_urls.py frontend/templates/pages/coming_soon.html educore/urls.py apps/core/tests/test_coming_soon.py
git commit -m "feat(core): add shared placeholder page for unbuilt nav destinations"
```

---

### Task 3: `base_console.html` + `console_nav.html` — the nav shell

**Files:**
- Create: `frontend/templates/base_console.html`
- Create: `frontend/templates/components/console_nav.html`
- Modify: `frontend/templates/pages/coming_soon.html:1` — change `{% extends "base.html" %}` to `{% extends "base_console.html" %}` if you used the temporary workaround in Task 2.
- Test: `apps/identity/tests/test_console_nav_render.py`

**Interfaces:**
- Consumes: `apps.identity.nav.get_nav_for_user` (Task 1). `base_console.html` expects the view to pass `nav_groups` (the return value of `get_nav_for_user`) into its context — OR (simpler, chosen here) a context processor computes it automatically so every view extending `base_console.html` gets it for free without remembering to pass it.
- Produces: `base_console.html` (block `content`, inherited from `base.html`), a context processor `apps.identity.context_processors.console_nav` registered in settings.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_console_nav_render.py
from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, RoleAssignment, School, User


class ConsoleNavRenderTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.teacher = User.objects.create(
            phone_e164='+6281300000004', full_name='Teacher Nav', foundation_id=self.foundation.id,
        )
        self.teacher.set_password('pw12345')
        self.teacher.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.teacher)

    def test_console_page_renders_nav_items_teacher_can_reach(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Antrean penilaian')
        self.assertContains(response, 'Kehadiran & gerbang')
        self.assertNotContains(response, 'Rekonsiliasi')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_nav_render -v 2`
Expected: FAIL — `TemplateDoesNotExist: base_console.html`

- [ ] **Step 3: Implement**

Create `apps/identity/context_processors.py`:

```python
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
```

In `educore/settings/base.py`, find the `TEMPLATES` setting's `context_processors` list and add `'apps.identity.context_processors.console_nav',` to it (alongside the existing `django.template.context_processors.*` and `django.contrib.auth.context_processors.auth` entries already there).

Create `frontend/templates/components/console_nav.html`:

```html
{% load i18n %}
<aside class="console-nav" style="width:248px;flex:none;background:#fff;border-right:1px solid #E5DDD9;display:flex;flex-direction:column;overflow-y:auto">
  <div style="padding:18px 16px;border-bottom:1px solid #E5DDD9;display:flex;align-items:center;gap:11px">
    <div style="width:22px;height:22px;background:#C8102E;flex:none"></div>
    <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:17px;letter-spacing:-.02em">EduCore</span>
  </div>
  <nav style="padding:12px 10px 20px;display:flex;flex-direction:column;gap:18px;flex:1">
    {% for group in nav_groups %}
    <div style="display:flex;flex-direction:column;gap:3px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;color:#A4998F;padding:4px 10px 6px">{{ group.label }}</span>
      {% for item in group.items %}
      <a href="{% url item.url_name %}" style="display:flex;align-items:center;gap:12px;padding:9px 10px;border-radius:6px;color:#5A4F49;text-decoration:none;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:500">{{ item.label }}</a>
      {% endfor %}
    </div>
    {% endfor %}
  </nav>
  <div style="border-top:1px solid #E5DDD9;padding:14px 16px;display:flex;align-items:center;gap:11px">
    <span style="width:34px;height:34px;background:#FDE7EA;color:#C8102E;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:14px;display:flex;align-items:center;justify-content:center;flex:none">{{ request.user.full_name|slice:":2"|upper }}</span>
    <span style="display:flex;flex-direction:column;gap:2px;min-width:0">
      <span style="font-size:13.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ request.user.full_name }}</span>
    </span>
  </div>
</aside>
```

Note: `{% url item.url_name %}` resolves a plain (non-namespaced) name like `permission-slip-console-page` directly, and a namespaced one like `console:coming_soon` also resolves correctly via `{% url %}`'s standard dotted-string lookup — Django's `{% url %}` tag accepts either form as a single string argument, no special-casing needed here.

Create `frontend/templates/base_console.html`:

```html
{% extends "base.html" %}
{% block content %}
<div style="display:flex;align-items:stretch;min-height:calc(100vh - 140px)">
  {% include "components/console_nav.html" %}
  <div style="flex:1;min-width:0;padding:24px">
    {% block console_content %}{% endblock %}
  </div>
</div>
{% endblock %}
```

`coming_soon.html` and every future console page override `{% block console_content %}`, not `{% block content %}` directly — re-open `frontend/templates/pages/coming_soon.html` and change its `{% block content %}`/`{% endblock %}` pair to `{% block console_content %}`/`{% endblock %}` (it already extends `base_console.html` from Task 2's Step 3, or from the temporary-workaround fix-up at the top of this task).

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_nav_render apps.core.tests.test_coming_soon apps.identity.tests.test_nav -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/context_processors.py frontend/templates/base_console.html frontend/templates/components/console_nav.html frontend/templates/pages/coming_soon.html educore/settings/base.py apps/identity/tests/test_console_nav_render.py
git commit -m "feat(identity): add console nav shell and context processor"
```

---

### Task 4: `apps.identity.landing` — per-role landing map

**Files:**
- Create: `apps/identity/landing.py`
- Modify: `apps/identity/web_views.py:31-59` (replace `_default_landing_url`'s body)
- Test: `apps/identity/tests/test_landing.py`

**Interfaces:**
- Consumes: `apps.identity.models.RoleAssignment` (existing), `apps.identity.rbac.has_permission` (existing, unchanged fallback path).
- Produces: `apps.identity.landing.ROLE_LANDING_URLS: dict[str, str]`, `apps.identity.landing.resolve_post_login_redirect(user, foundation_id) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_landing.py
from django.test import TestCase
from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.landing import resolve_post_login_redirect


class ResolvePostLoginRedirectTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)

    def _assign(self, user, role):
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=role,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )

    def test_foundation_admin_lands_on_overview(self):
        user = User.objects.create(phone_e164='+6281300000010', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/overview/')

    def test_higher_priority_role_wins_when_user_holds_two(self):
        user = User.objects.create(phone_e164='+6281300000011', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_TEACHER)
        self._assign(user, RoleAssignment.ROLE_FOUNDATION_ADMIN)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/overview/')

    def test_canteen_operator_lands_on_console_home(self):
        user = User.objects.create(phone_e164='+6281300000012', full_name='U', foundation_id=self.foundation.id)
        self._assign(user, RoleAssignment.ROLE_CANTEEN_OPERATOR)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/')

    def test_no_role_assignment_falls_back_to_grades_read_check(self):
        user = User.objects.create(phone_e164='+6281300000013', full_name='U', foundation_id=self.foundation.id)
        self.assertEqual(resolve_post_login_redirect(user, self.foundation.id), '/web/home/')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_landing -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.identity.landing'`

- [ ] **Step 3: Implement**

First, read `apps/identity/web_views.py:1-60` to see the exact current `_default_landing_url` body and its imports (`has_permission`, `RoleAssignment`, `SCOPE_SCHOOL`, `NO_CONSOLE_PAGE_URL`, `DEFAULT_REDIRECT_URL` are already imported/defined there from PR #187 — do not re-declare them, import from `.landing` instead where this task needs them).

Create `apps/identity/landing.py`:

```python
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
# dict insertion order IS the priority order: foundation_admin outranks
# school_admin outranks finance_officer outranks teacher outranks the
# three roles that share the generic /web/home/ landing.
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
```

Replace `_default_landing_url`'s body in `apps/identity/web_views.py` (keep the function itself — `landing.py` calls it as the final fallback — but change its two `return DEFAULT_REDIRECT_URL` lines to delegate to the new map first). Modify it to:

```python
def _default_landing_url(user, foundation_id):
    """grades.read-based fallback for a user with NO RoleAssignment row at
    all (resolve_post_login_redirect in .landing handles every user who
    actually holds a role — this function is now only that one edge case,
    kept here rather than moved to avoid disturbing its 3 existing call
    sites' import paths)."""
    if not foundation_id:
        return NO_CONSOLE_PAGE_URL

    if has_permission(user, 'grades.read', foundation_id, school_id=None):
        return DEFAULT_REDIRECT_URL

    assigned_schools = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        scope_type=SCOPE_SCHOOL,
        deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    for school_id in assigned_schools:
        if has_permission(user, 'grades.read', foundation_id, school_id=school_id):
            return DEFAULT_REDIRECT_URL

    return NO_CONSOLE_PAGE_URL
```

(This is character-for-character what PR #187 already shipped — confirm it matches before moving on; the only actual change in this task is at the 3 call sites below.)

At each of `_default_landing_url`'s 3 call sites in `apps/identity/web_views.py` (`WebLoginView.get`, `WebLoginView.post`, `WebSSOLoginView.post` — grep the file for `_default_landing_url(` to find the exact current lines), replace the call `_default_landing_url(request.user, ...)` / `_default_landing_url(user, ...)` with `resolve_post_login_redirect(request.user, ...)` / `resolve_post_login_redirect(user, ...)`, and add the import `from .landing import resolve_post_login_redirect` near the top of the file alongside the existing `from .models import RoleAssignment` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_landing apps.identity.tests.test_web_views -v 2`
Expected: all pass — including PR #187's existing web_views tests for the 3-previously-403 roles, now landing on their real role URLs (`/web/home/overview/`, `/web/home/`, etc.) instead of `/web/home/` uniformly. If any existing assertion in `test_web_views.py` hardcodes `/web/home/` for a role that now has its own URL (e.g. `foundation_admin`), update that assertion — it is testing the old stopgap behavior PR #187 shipped, which this task deliberately supersedes.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/landing.py apps/identity/web_views.py apps/identity/tests/test_landing.py apps/identity/tests/test_web_views.py
git commit -m "feat(identity): replace binary post-login redirect with per-role landing map"
```

---

### Task 5: Landing pages for foundation_admin / school_admin / teacher / finance_officer

**Files:**
- Modify: `apps/identity/web_views.py` (append 4 views + `get_landing_stats`)
- Create: `frontend/templates/pages/console_landing.html` (one shared template, parameterized by context)
- Modify: `educore/urls.py` (4 new paths under `/web/home/`)
- Test: `apps/identity/tests/test_console_landing.py`

**Interfaces:**
- Consumes: `apps.identity.landing.ROLE_LANDING_URLS` (Task 4, for the URL paths themselves), `base_console.html` (Task 3).
- Produces: `apps.identity.web_views.get_landing_stats(foundation_id) -> dict` with keys `student_count: int`, `overdue_invoice_count: int`, `alpa_today_count: int`. Four new views: `FoundationOverviewLandingView`, `SchoolAdminTodayLandingView`, `TeacherAgendaLandingView`, `FinanceBillingLandingView`, all `LoginRequiredMixin` + `TemplateView`, mounted at `/web/home/overview/`, `/web/home/today/`, `/web/home/agenda/`, `/web/home/billing/` respectively.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_console_landing.py
import datetime
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User


class ConsoleLandingPagesTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Student One')
        self.student = Student.objects.create(
            foundation_id=self.foundation.id, school=self.school, person=person,
            nis='0001', status=Student.STATUS_ACTIVE,
        )
        self.user = User.objects.create(
            phone_e164='+6281300000020', full_name='Chair', foundation_id=self.foundation.id,
        )
        self.user.set_password('pw12345')
        self.user.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.client.force_login(self.user)

    def test_foundation_overview_shows_real_student_count(self):
        response = self.client.get(reverse('console-home-overview'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1')  # student_count

    def test_finance_billing_shows_overdue_invoice_count(self):
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number='INV/S1/2026/000001', period='2026-08',
            issue_date=timezone.now().date() - datetime.timedelta(days=40),
            due_date=timezone.now().date() - datetime.timedelta(days=10),
            status=InvoiceStatus.ISSUED,
        )
        response = self.client.get(reverse('console-home-billing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tagihan & pembayaran')

    def test_teacher_agenda_page_renders(self):
        response = self.client.get(reverse('console-home-agenda'))
        self.assertEqual(response.status_code, 200)

    def test_school_admin_today_shows_alpa_count(self):
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=timezone.localdate(), status=AttendanceStatus.ALPA,
        )
        response = self.client.get(reverse('console-home-today'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1')  # alpa_today_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_landing -v 2`
Expected: FAIL — `NoReverseMatch: 'console-home-overview' is not a registered namespace`

- [ ] **Step 3: Implement**

Append to `apps/identity/web_views.py`:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.utils import timezone

from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Student


def get_landing_stats(foundation_id):
    """Three simple, single-model aggregate counts shared by every real-data
    landing page. Deliberately minimal (see design spec's Non-goals: no new
    cross-module aggregate services in this slice) — each is one filtered
    .count() call, safe to run on every login-redirect landing hit."""
    return {
        'student_count': Student.objects.filter(status=Student.STATUS_ACTIVE).count(),
        'overdue_invoice_count': Invoice.objects.filter(
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
            due_date__lt=timezone.localdate(),
        ).count(),
        'alpa_today_count': AttendanceDay.objects.filter(
            date=timezone.localdate(), status=AttendanceStatus.ALPA,
        ).count(),
    }


class _ConsoleLandingView(LoginRequiredMixin, TemplateView):
    template_name = 'pages/console_landing.html'
    page_title = ''
    greeting_key = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        ctx['stats'] = get_landing_stats(foundation_id) if foundation_id else {
            'student_count': 0, 'overdue_invoice_count': 0, 'alpa_today_count': 0,
        }
        ctx['page_title'] = self.page_title
        return ctx


class FoundationOverviewLandingView(_ConsoleLandingView):
    page_title = 'Ikhtisar yayasan'


class SchoolAdminTodayLandingView(_ConsoleLandingView):
    page_title = 'Hari ini'


class TeacherAgendaLandingView(_ConsoleLandingView):
    page_title = 'Agenda hari ini'


class FinanceBillingLandingView(_ConsoleLandingView):
    page_title = 'Tagihan & pembayaran'
```

(`get_current_foundation_id` is already imported at the top of `apps/identity/web_views.py` from PR #187's `set_current_foundation_id` import line — extend that import to also bring in `get_current_foundation_id` from `educore.middleware.tenancy`.)

Create `frontend/templates/pages/console_landing.html`:

```html
{% extends "base_console.html" %}
{% load i18n %}

{% block title %}{{ page_title }}{% endblock %}

{% block console_content %}
<div style="display:flex;flex-direction:column;gap:24px;max-width:960px">
  <h1 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:28px;letter-spacing:-.03em">
    {% blocktranslate with name=request.user.full_name %}Selamat pagi, {{ name }}.{% endblocktranslate %}
  </h1>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px">
    <div style="background:#fff;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:6px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8E8178">{% translate "Siswa aktif" %}</span>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:26px">{{ stats.student_count }}</span>
    </div>
    <div style="background:#fff;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:6px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8E8178">{% translate "Tagihan jatuh tempo" %}</span>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:26px">{{ stats.overdue_invoice_count }}</span>
    </div>
    <div style="background:#fff;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:6px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8E8178">{% translate "Alpa hari ini" %}</span>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:26px">{{ stats.alpa_today_count }}</span>
    </div>
  </div>
</div>
{% endblock %}
```

In `educore/urls.py`, add (near the existing `path('web/home/', WebConsoleHomeView.as_view(), name='web-console-home'),` line):

```python
    path('web/home/overview/', FoundationOverviewLandingView.as_view(), name='console-home-overview'),
    path('web/home/today/', SchoolAdminTodayLandingView.as_view(), name='console-home-today'),
    path('web/home/agenda/', TeacherAgendaLandingView.as_view(), name='console-home-agenda'),
    path('web/home/billing/', FinanceBillingLandingView.as_view(), name='console-home-billing'),
```

Import the four new view classes in `educore/urls.py`'s existing `from apps.identity.web_views import FoundationMicrosoftTenantSettingsView, WebConsoleHomeView, WebLoginView` line — extend it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_landing -v 2`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/web_views.py frontend/templates/pages/console_landing.html educore/urls.py apps/identity/tests/test_console_landing.py
git commit -m "feat(identity): add real per-role landing pages with live aggregate stats"
```

---

### Task 6: Generic landing for counsellor / canteen_operator / clinic_officer

**Files:**
- Modify: `apps/identity/web_views.py::WebConsoleHomeView` (extend `get`)
- Modify: `frontend/templates/pages/console_home.html`
- Test: `apps/identity/tests/test_console_home_nav_grid.py`

**Interfaces:**
- Consumes: `apps.identity.nav.get_nav_for_user` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_console_home_nav_grid.py
from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, RoleAssignment, School, User


class ConsoleHomeNavGridTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.user = User.objects.create(
            phone_e164='+6281300000030', full_name='Canteen', foundation_id=self.foundation.id,
        )
        self.user.set_password('pw12345')
        self.user.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=RoleAssignment.ROLE_CANTEEN_OPERATOR,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.user)

    def test_console_home_lists_users_own_nav_items(self):
        response = self.client.get(reverse('web-console-home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kantin & dompet')
        self.assertNotContains(response, 'Rekonsiliasi')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_home_nav_grid -v 2`
Expected: FAIL — `AssertionError: False is not true : Couldn't find 'Kantin & dompet'`

- [ ] **Step 3: Implement**

Find `WebConsoleHomeView` in `apps/identity/web_views.py` (its `get` currently does `return render(request, 'pages/console_home.html', {})`). Change it to:

```python
class WebConsoleHomeView(View):
    """GET /web/home/ — default landing for roles with no dedicated web console page yet.

    Extended (Task 6, web-console-nav-and-landing) to also list the user's
    own permission-gated nav items as a simple link grid, instead of just
    the static "no page for your role" message — this is the generic
    landing for counsellor/canteen_operator/clinic_officer.
    """

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        nav_groups = get_nav_for_user(request.user, foundation_id) if foundation_id else []
        return render(request, 'pages/console_home.html', {'nav_groups': nav_groups})
```

Add `from .nav import get_nav_for_user` to `apps/identity/web_views.py`'s imports.

Update `frontend/templates/pages/console_home.html` to add the link grid below the existing message:

```html
{% extends "base.html" %}
{% load i18n %}

{% block title %}{% translate "EduCore — Konsol" %}{% endblock %}

{% block content %}
<div style="max-width: 640px; margin: 64px auto; text-align: center;">
  <h1 class="font-display text-h2" style="margin-bottom: 12px;">
    {% blocktranslate with name=request.user.full_name %}Selamat datang, {{ name }}{% endblocktranslate %}
  </h1>
  <p class="text-body-sm text-ink-700">
    {% translate "Belum ada halaman konsol web khusus untuk peran Anda saat ini. Hubungi Tata Usaha (TU) sekolah jika Anda memerlukan akses ke fitur tertentu." %}
  </p>
</div>
{% if nav_groups %}
<div style="max-width: 640px; margin: 0 auto 64px; display:flex; flex-direction:column; gap:20px;">
  {% for group in nav_groups %}
  <div>
    <h2 style="font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#8E8178;margin-bottom:8px">{{ group.label }}</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px">
      {% for item in group.items %}
      <a href="{% url item.url_name %}" style="padding:14px 12px;border:1px solid #E5DDD9;background:#fff;color:#16110F;text-decoration:none;font-family:'IBM Plex Sans',sans-serif;font-size:13.5px;font-weight:600">{{ item.label }}</a>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}
{% endblock %}
```

Note this template still extends `base.html` directly (not `base_console.html`) — `WebConsoleHomeView` predates the nav shell and this task only adds the link grid, it does not migrate this page's own chrome. Migrating it to `base_console.html` would double-render the nav (once in the header shell, once as this page's own content grid) — leave as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_home_nav_grid apps.identity.tests.test_web_views -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/web_views.py frontend/templates/pages/console_home.html apps/identity/tests/test_console_home_nav_grid.py
git commit -m "feat(identity): list user's own nav items on the generic console home"
```

---

### Task 7: Language control in the console header

**Files:**
- Modify: `frontend/templates/base_console.html`
- Test: `apps/identity/tests/test_console_language_toggle.py`

**Interfaces:**
- Consumes: the existing `set_language` Django view (already used by `base.html`'s own ID/EN buttons — same mechanism, this task just adds an equivalent control inside the console shell's top bar per the design's visual placement).

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_console_language_toggle.py
from django.test import TestCase
from django.urls import reverse
from apps.identity.models import Foundation, User


class ConsoleLanguageToggleTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.user = User.objects.create(phone_e164='+6281300000040', full_name='U', foundation_id=self.foundation.id)
        self.user.set_password('pw12345')
        self.user.save()
        self.client.force_login(self.user)

    def test_console_page_has_language_form_targeting_set_language(self):
        response = self.client.get(reverse('console:coming_soon'))
        self.assertContains(response, reverse('set_language'))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_language_toggle -v 2`
Expected: FAIL — the console header currently has no `set_language` form of its own (only `base.html`'s own top-level header does, which this console page's `content` block replaces).

- [ ] **Step 3: Implement**

Note: `base.html`'s existing header (with its own `set_language` form) is still rendered above the nav shell for console pages too, since `base_console.html` only overrides `{% block content %}`, not the header. Verify this first: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_language_toggle -v 2` — if it unexpectedly already passes because `base.html`'s own header form is present on every page including console ones, this task is a no-op confirmation, not a new feature; write that finding into your report instead of adding a duplicate control. Only add a second language form inside `console_nav.html`'s top bar if the design's visual placement (in the console's own sticky header, not the outer `base.html` header) is something you're asked to match exactly — the design spec's scope decision 3 only requires ONE real i18n mechanism, not a specific visual location, so confirming the existing header's form is reachable from every console page satisfies the requirement without new code.

If the test fails for a real reason (e.g. `base_console.html`'s content block structure somehow hides the outer header — check by rendering and inspecting), add this to `console_nav.html`'s top section instead of duplicating a new one, reusing the exact same form `base.html` already has:

```html
<form action="{% url 'set_language' %}" method="post" style="display: flex; align-items: center; gap: 4px; padding: 14px 16px; border-bottom: 1px solid #E5DDD9;">
  {% csrf_token %}
  <input type="hidden" name="next" value="{{ request.get_full_path }}">
  <button type="submit" name="language" value="id" style="padding: 4px 8px; border: 1px solid #E5DDD9; background: {% if LANGUAGE_CODE == 'id' %}#C8102E; color: #fff;{% else %}transparent;{% endif %} font-size: 0.75rem; font-weight: 600; cursor: pointer;">ID</button>
  <button type="submit" name="language" value="en" style="padding: 4px 8px; border: 1px solid #E5DDD9; background: {% if LANGUAGE_CODE == 'en' %}#C8102E; color: #fff;{% else %}transparent;{% endif %} font-size: 0.75rem; font-weight: 600; cursor: pointer;">EN</button>
</form>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity.tests.test_console_language_toggle -v 2`
Expected: 1 passed.

- [ ] **Step 5: Commit (only if Step 3 required a real code change)**

```bash
git add frontend/templates/components/console_nav.html apps/identity/tests/test_console_language_toggle.py
git commit -m "test(identity): confirm console pages reach the existing language toggle"
```

---

### Task 8: Migrate Permission Slip Console to the nav shell + final regression pass

**Files:**
- Modify: `frontend/templates/pages/permission_slip_console_page.html:1`
- No other new files. Verifies Tasks 1–7 are fully wired.

- [ ] **Step 1: Migrate the one real console page**

Change `frontend/templates/pages/permission_slip_console_page.html`'s first line from `{% extends "base.html" %}` to `{% extends "base_console.html" %}`, and change its `{% block content %}`/`{% endblock %}` pair to `{% block console_content %}`/`{% endblock %}` (grep the file for both to find them — it's the only page in this task migrating to the new shell, since it's the only page with a real, already-shipped console feature behind it).

- [ ] **Step 2: Run the permission-slip console's existing test suite**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.academic.tests.test_permission_slip_console apps.academic.tests.test_permission_slips -v 2`
Expected: all pass, unchanged pass count from before this task (this is a template-inheritance-only change; no view or URL logic changed).

- [ ] **Step 3: System check**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py check`
Expected: `System check identified no issues.`

- [ ] **Step 4: Migrations check**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py makemigrations --check --dry-run`
Expected: exits 0, no output (this feature adds no models).

- [ ] **Step 5: Full apps.identity + apps.core + apps.academic suite**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.identity apps.core apps.academic -v 2`
Expected: all pass, no regressions in `apps.identity`'s existing RBAC/permission tests or PR #187's own web_views tests.

- [ ] **Step 6: Full project suite**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test`
Expected: same known baseline as every recent PR in this repo — 2 pre-existing, unrelated `apps.finance` failures (`test_api_cancel_and_write_off`, `test_discount_approval_action`), already on `main`, confirmed via this repo's memory (`memory/01_PROJECT.md`). Do NOT use `git stash` to verify this in a shared worktree — compare your failure names/count directly against that known baseline instead. Any failure beyond those two names is a real regression from this plan's work — investigate and fix it, don't dismiss it.

- [ ] **Step 7: Commit (only if a real fix was needed)**

```bash
git add -A
git commit -m "fix: address regressions found in final wiring/regression pass"
```

Otherwise this task is verification-only and produces no diff beyond Step 1's migration — commit that separately:

```bash
git add frontend/templates/pages/permission_slip_console_page.html
git commit -m "refactor(academic): migrate permission slip console to the console nav shell"
```

---

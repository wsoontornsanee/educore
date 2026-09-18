# Service Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a public, database-backed EduCore service status page (`/status/`) with component health, uptime/latency metrics, incident history, and email capture, plus the minimal platform-wide RBAC addition it needs.

**Architecture:** A new non-tenant `apps.status` app (models are plain `models.Model`, not `TenantModel` — status data has no `foundation_id`) holds `ServiceComponent`/`ComponentHeartbeat`/`DailyComponentStatus`/`StatusIncident`/`StatusSubscriber`. A `*/5` cron command probes DB connectivity and rolls today's per-component status. A public `TemplateView` renders the page; a small session-auth "web" surface (mirroring `apps.academic.web_views`' `APIView` + `render()` pattern) lets staff edit component overrides and incidents, gated by a new platform-wide permission that bypasses the tenant-scoped `RoleAssignment` table entirely via a new, deliberately non-tenant `PlatformRoleAssignment` model in `apps.identity`.

**Tech Stack:** Django 5.x, MySQL 8 (SQLite for fast tests per `EDUCORE_USE_SQLITE=1`), DRF (`HasRequiredPermission`), Django templates extending `marketing/base.html`.

Full design context: `docs/superpowers/specs/2026-09-18-service-status-page-design.md`.

## Global Constraints

- Single Django monolith, MySQL 8.0 only, no Redis/Celery/brokers — background work is cron-driven management commands only (`00_CORE.md` §1).
- `apps.status` models are plain `models.Model`, **not** `TenantModel` — this is platform data with no `foundation_id`, matching the existing precedent of `apps.core.AuditEvent`/`DomainEvent`/`TaskQueue`/`JobRun`/`IdempotencyRecord` and `apps.identity.OTPChallenge`. Do not give these models a `foundation_id` field or route them through `TenantManager`.
- Every scheduled command acquires `GET_LOCK` via `apps.core.locks.advisory_lock` and exits cleanly if held; logs a `apps.core.models.JobRun` row (`00_CORE.md` §5, `ARC-007`/`ARC-008`).
- Every mutating action (incident create/update) calls `apps.core.services.audit(...)` explicitly with `actor_id`/`role` (AGENTS.md red line #4). No hard deletes anywhere (AGENTS.md red line #2) — this feature has no deletes at all, so this is automatically satisfied.
- `id-ID` first: all user-facing copy has an Indonesian source and an English translation (AGENTS.md §3.6). Reuse the exact wording already written for the `st_*`/`cmp_*`/`inc_*`/`sev_*` keys in the source design mockup — do not re-translate.
- `RoleAssignment`, `TenantManager`, and `TenancyMiddleware` are not modified by this feature. `PlatformRoleAssignment` is strictly additive.
- Tests use `python manage.py test` or `pytest` (`EDUCORE_USE_SQLITE=1` for fast local runs). Target ≥80% coverage on `apps/status` business logic (USER.md §2).
- Every task ends with a commit. Follow Conventional Commits (`feat:`, `test:`, `fix:`) matching this repo's existing history.

---

### Task 1: `PlatformRoleAssignment` model (apps.identity)

**Files:**
- Modify: `apps/identity/models.py` (append new model at end of file)
- Create: `apps/identity/migrations/00XX_platformroleassignment.py` (run `makemigrations`, do not hand-write)
- Create: `apps/identity/admin.py` (new file — none exists yet in this app)
- Test: `apps/identity/tests/test_platform_role_assignment.py`

**Interfaces:**
- Produces: `apps.identity.models.PlatformRoleAssignment` with class attribute `ROLE_PLATFORM_OPERATOR = 'platform_operator'`, fields `user` (FK to `User`), `role` (CharField, choices), `created_at` (auto_now_add). `unique_together`-style constraint on `(user, role)`.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_platform_role_assignment.py
from django.db import IntegrityError
from django.test import TestCase
from apps.identity.models import PlatformRoleAssignment, User


class PlatformRoleAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(phone_e164='+6281200000001', full_name='Ops One')

    def test_create_platform_operator_assignment(self):
        assignment = PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        self.assertEqual(assignment.role, 'platform_operator')
        self.assertIsNotNone(assignment.created_at)

    def test_duplicate_user_role_rejected(self):
        PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        with self.assertRaises(IntegrityError):
            PlatformRoleAssignment.objects.create(
                user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
            )

    def test_str_representation(self):
        assignment = PlatformRoleAssignment.objects.create(
            user=self.user, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )
        self.assertIn('platform_operator', str(assignment))
        self.assertIn('PLATFORM', str(assignment))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_platform_role_assignment -v 2`
Expected: FAIL — `ImportError: cannot import name 'PlatformRoleAssignment'`

- [ ] **Step 3: Write the model**

Append to `apps/identity/models.py`:

```python
class PlatformRoleAssignment(models.Model):
    """Platform-wide (non-tenant) role assignment for EduCore operator staff.

    Deliberately NOT a TenantModel: a platform role has no foundation_id and
    must never be routed through TenantManager's fail-closed tenant scoping.
    See docs/superpowers/specs/2026-09-18-service-status-page-design.md §4.
    """
    ROLE_PLATFORM_OPERATOR = 'platform_operator'
    ROLE_CHOICES = [
        (ROLE_PLATFORM_OPERATOR, 'Platform Operator'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='platform_role_assignments')
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'platform_role_assignments'
        verbose_name = 'Penugasan Peran Platform'
        verbose_name_plural = 'Daftar Penugasan Peran Platform'
        constraints = [
            models.UniqueConstraint(fields=['user', 'role'], name='unique_user_platform_role')
        ]

    def __str__(self):
        return f"{self.user.phone_e164} -> {self.role} (PLATFORM)"
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations apps.identity`
Expected: creates `apps/identity/migrations/00XX_platformroleassignment.py`.

Create `apps/identity/admin.py`:

```python
from django.contrib import admin
from .models import PlatformRoleAssignment


@admin.register(PlatformRoleAssignment)
class PlatformRoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__phone_e164', 'user__full_name')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_platform_role_assignment -v 2`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/models.py apps/identity/admin.py apps/identity/migrations/ apps/identity/tests/test_platform_role_assignment.py
git commit -m "feat(identity): add non-tenant PlatformRoleAssignment model"
```

---

### Task 2: Platform permission resolution + `HasRequiredPermission` hook

**Files:**
- Modify: `apps/identity/rbac.py` (append new section)
- Modify: `apps/identity/permissions.py:1-13` (import) and the `has_permission` body (insert platform check)
- Test: `apps/identity/tests/test_platform_rbac.py`

**Interfaces:**
- Consumes: `apps.identity.models.PlatformRoleAssignment` (Task 1).
- Produces: `apps.identity.rbac.has_platform_permission(user, permission_key: str) -> bool`, `apps.identity.rbac.get_platform_permissions(user) -> Set[str]`, `apps.identity.rbac.PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]]` containing `{'platform_operator': {'status.write'}}`.

- [ ] **Step 1: Write the failing test**

```python
# apps/identity/tests/test_platform_rbac.py
from django.test import TestCase
from apps.identity.models import PlatformRoleAssignment, User
from apps.identity.rbac import has_platform_permission, get_platform_permissions


class PlatformRbacTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create(phone_e164='+6281200000002', full_name='Ops Two')
        self.plain_user = User.objects.create(phone_e164='+6281200000003', full_name='Plain User')
        self.superuser = User.objects.create(
            phone_e164='+6281200000004', full_name='Super User', is_superuser=True,
        )
        PlatformRoleAssignment.objects.create(
            user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR,
        )

    def test_operator_has_status_write(self):
        self.assertTrue(has_platform_permission(self.operator, 'status.write'))

    def test_plain_user_lacks_status_write(self):
        self.assertFalse(has_platform_permission(self.plain_user, 'status.write'))

    def test_unknown_permission_key_denied_even_for_operator(self):
        self.assertFalse(has_platform_permission(self.operator, 'not.a.real.permission'))

    def test_superuser_has_all_platform_permissions(self):
        self.assertTrue(has_platform_permission(self.superuser, 'status.write'))
        self.assertIn('status.write', get_platform_permissions(self.superuser))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_platform_rbac -v 2`
Expected: FAIL — `ImportError: cannot import name 'has_platform_permission'`

- [ ] **Step 3: Implement**

In `apps/identity/rbac.py`, add near the top import and at the end of the file:

```python
from .models import RoleAssignment, User, PlatformRoleAssignment
```

(replace the existing `from .models import RoleAssignment, User` import line with the above)

```python
# Platform-wide (non-tenant) roles and permissions — see
# docs/superpowers/specs/2026-09-18-service-status-page-design.md §4.
# Deliberately separate from ROLE_PERMISSIONS/RoleAssignment: a platform
# permission never requires (or implies) a foundation_id.
PLATFORM_ROLE_OPERATOR = PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR

PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    PLATFORM_ROLE_OPERATOR: {'status.write'},
}


def get_platform_permissions(user) -> Set[str]:
    """Calculate the cumulative set of platform-wide permission keys for a user."""
    if not user or not user.is_authenticated or not user.is_active or user.is_locked:
        return set()

    if user.is_superuser:
        all_perms: Set[str] = set()
        for perms in PLATFORM_ROLE_PERMISSIONS.values():
            all_perms.update(perms)
        return all_perms

    assignments = PlatformRoleAssignment.objects.filter(user=user)
    permissions: Set[str] = set()
    for assignment in assignments:
        permissions.update(PLATFORM_ROLE_PERMISSIONS.get(assignment.role, set()))
    return permissions


def has_platform_permission(user, permission_key: str) -> bool:
    """True if user holds permission_key via a platform-wide role (no tenant context needed)."""
    return permission_key in get_platform_permissions(user)
```

In `apps/identity/permissions.py`, change the import line:

```python
from .rbac import has_permission, has_platform_permission, ROLE_FOUNDATION_ADMIN, SCOPE_FOUNDATION, SCOPE_SCHOOL
```

Then in `HasRequiredPermission.has_permission`, insert immediately after the fail-closed `required_permission` check and before the `# Resolve foundation context` comment:

```python
        if not required_permission:
            raise PermissionDenied("Akses ditolak: Handler API tidak mendefinisikan required_permission (IAM-010).")

        # Platform-wide permission short-circuit (no tenant context required) —
        # see docs/superpowers/specs/2026-09-18-service-status-page-design.md §4.
        if has_platform_permission(request.user, required_permission):
            return True

        # Resolve foundation context
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_platform_rbac apps.identity.tests -v 2`
Expected: new tests pass; full `apps.identity` suite still green (regression check — the inserted branch must not affect any existing foundation/school-scoped permission test).

- [ ] **Step 5: Commit**

```bash
git add apps/identity/rbac.py apps/identity/permissions.py apps/identity/tests/test_platform_rbac.py
git commit -m "feat(identity): add platform-wide permission resolution for HasRequiredPermission"
```

---

### Task 3: `apps.status` app scaffold + `ServiceComponent` model

**Files:**
- Create: `apps/status/__init__.py`
- Create: `apps/status/apps.py`
- Create: `apps/status/models.py`
- Create: `apps/status/admin.py`
- Create: `apps/status/migrations/__init__.py`
- Create: `apps/status/migrations/0001_initial.py` (via `makemigrations`)
- Create: `apps/status/migrations/0002_seed_components.py` (data migration, hand-written)
- Create: `apps/status/tests/__init__.py`
- Create: `apps/status/tests/test_models.py`
- Modify: `educore/settings/base.py:68` (append `'apps.status.apps.StatusConfig',` to `INSTALLED_APPS`, right after the `apps.calendar_sync` line)

**Interfaces:**
- Produces: `apps.status.models.ServiceComponent` with `STATUS_OPERATIONAL`/`STATUS_DEGRADED`/`STATUS_DOWN` class constants, `STATUS_CHOICES`, fields `key` (unique slug), `name_id`, `name_en`, `note_id`, `note_en`, `display_order`, `manual_status` (nullable).

- [ ] **Step 1: Scaffold the app**

```bash
mkdir -p apps/status/migrations apps/status/tests apps/status/management/commands
touch apps/status/__init__.py apps/status/migrations/__init__.py apps/status/tests/__init__.py
touch apps/status/management/__init__.py apps/status/management/commands/__init__.py
```

`apps/status/apps.py`:

```python
from django.apps import AppConfig


class StatusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.status'
    label = 'status'
```

In `educore/settings/base.py`, change:

```python
    'apps.calendar_sync.apps.CalendarSyncConfig',
]
```

to:

```python
    'apps.calendar_sync.apps.CalendarSyncConfig',
    'apps.status.apps.StatusConfig',
]
```

- [ ] **Step 2: Write the failing test**

```python
# apps/status/tests/test_models.py
from django.test import TestCase
from apps.status.models import ServiceComponent


class ServiceComponentTests(TestCase):
    def test_create_component(self):
        component = ServiceComponent.objects.create(
            key='web_portal', name_id='Portal web', name_en='Web portal',
            note_id='Dasbor yayasan', note_en='Foundation dashboard', display_order=1,
        )
        self.assertEqual(component.manual_status, None)
        self.assertEqual(str(component), 'Portal web')

    def test_key_is_unique(self):
        ServiceComponent.objects.create(key='web_portal', name_id='A', name_en='A')
        with self.assertRaises(Exception):
            ServiceComponent.objects.create(key='web_portal', name_id='B', name_en='B')

    def test_ordering_by_display_order(self):
        c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2', display_order=2)
        c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1', display_order=1)
        self.assertEqual(list(ServiceComponent.objects.all()), [c1, c2])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_models -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.status.models'`

- [ ] **Step 4: Write the model + admin + migrations**

`apps/status/models.py`:

```python
"""Public service status page models (platform-wide, non-tenant).

None of these models are TenantModel — status data has no foundation_id,
matching apps.core.AuditEvent/DomainEvent/JobRun/IdempotencyRecord and
apps.identity.OTPChallenge. See
docs/superpowers/specs/2026-09-18-service-status-page-design.md.
"""
from django.db import models


class ServiceComponent(models.Model):
    STATUS_OPERATIONAL = 'OPERATIONAL'
    STATUS_DEGRADED = 'DEGRADED'
    STATUS_DOWN = 'DOWN'
    STATUS_CHOICES = [
        (STATUS_OPERATIONAL, 'Operational'),
        (STATUS_DEGRADED, 'Degraded'),
        (STATUS_DOWN, 'Down'),
    ]

    key = models.SlugField(max_length=64, unique=True)
    name_id = models.CharField(max_length=128)
    name_en = models.CharField(max_length=128)
    note_id = models.CharField(max_length=255, blank=True, default='')
    note_en = models.CharField(max_length=255, blank=True, default='')
    display_order = models.PositiveIntegerField(default=0)
    manual_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, null=True, blank=True,
        help_text="Staff override. Null means derive status from the daily heartbeat rollup.",
    )

    class Meta:
        db_table = 'status_components'
        ordering = ['display_order', 'id']
        verbose_name = 'Komponen Layanan'
        verbose_name_plural = 'Daftar Komponen Layanan'

    def __str__(self):
        return self.name_id
```

`apps/status/admin.py`:

```python
from django.contrib import admin
from .models import ServiceComponent


@admin.register(ServiceComponent)
class ServiceComponentAdmin(admin.ModelAdmin):
    list_display = ('key', 'name_id', 'display_order', 'manual_status')
    list_editable = ('manual_status',)
    ordering = ('display_order',)
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations apps.status`
Expected: creates `apps/status/migrations/0001_initial.py`.

Hand-write the seed data migration `apps/status/migrations/0002_seed_components.py` (depends on `0001_initial`):

```python
from django.db import migrations

COMPONENTS = [
    dict(key='web_portal', display_order=1,
         name_id='Portal web', name_en='Web portal',
         note_id='Dasbor yayasan, admin sekolah, dan suite guru',
         note_en='Foundation dashboard, school admin, and teacher suite'),
    dict(key='partner_api', display_order=2,
         name_id='Partner API', name_en='Partner API',
         note_id='REST v1 dan pengiriman webhook',
         note_en='REST v1 and webhook delivery'),
    dict(key='mobile_apps', display_order=3,
         name_id='Aplikasi orang tua & guru', name_en='Parent & teacher apps',
         note_id='Sinkronisasi seluler dan antrean offline',
         note_en='Mobile sync and the offline queue'),
    dict(key='notifications', display_order=4,
         name_id='Notifikasi', name_en='Notifications',
         note_id='Push, SMS, dan surel — antrean sedang tertunda',
         note_en='Push, SMS and email — queue currently delayed'),
    dict(key='payments', display_order=5,
         name_id='Pembayaran', name_en='Payments',
         note_id='Virtual account, QRIS, dan rekonsiliasi',
         note_en='Virtual accounts, QRIS, and reconciliation'),
    dict(key='canteen_pos', display_order=6,
         name_id='POS kantin', name_en='Canteen POS',
         note_id='Terminal kantin dan sinkronisasi dompet',
         note_en='Canteen terminals and wallet sync'),
]


def seed_components(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    for data in COMPONENTS:
        ServiceComponent.objects.get_or_create(key=data['key'], defaults=data)


def remove_components(apps, schema_editor):
    ServiceComponent = apps.get_model('status', 'ServiceComponent')
    ServiceComponent.objects.filter(key__in=[c['key'] for c in COMPONENTS]).delete()


class Migration(migrations.Migration):
    dependencies = [('status', '0001_initial')]
    operations = [migrations.RunPython(seed_components, remove_components)]
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py migrate apps.status`

- [ ] **Step 5: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_models -v 2`
Expected: 3 passed.

Also run: `EDUCORE_USE_SQLITE=1 python manage.py check`
Expected: `System check identified no issues.`

- [ ] **Step 6: Commit**

```bash
git add apps/status/ educore/settings/base.py
git commit -m "feat(status): scaffold apps.status with ServiceComponent model + seed data"
```

---

### Task 4: `ComponentHeartbeat` + `DailyComponentStatus` models + query/rollup services

**Files:**
- Modify: `apps/status/models.py` (append two models)
- Modify: `apps/status/admin.py` (register both)
- Create: `apps/status/services.py`
- Test: `apps/status/tests/test_services.py`

**Interfaces:**
- Consumes: `ServiceComponent` (Task 3).
- Produces:
  - `apps.status.models.ComponentHeartbeat(component, checked_at, is_up, latency_ms)`
  - `apps.status.models.DailyComponentStatus(component, date, status)`, unique on `(component, date)`
  - `apps.status.services.record_heartbeats() -> int` (count of heartbeats created)
  - `apps.status.services.rollup_daily_status(date=None) -> int` (count of components rolled up)
  - `apps.status.services.get_component_status(component, date=None) -> str`
  - `apps.status.services.get_component_bars(component, days=30, as_of=None) -> list[dict]` (`{'date': date, 'color': '#hex'}`)
  - `apps.status.services.get_uptime_percentage(days=90, as_of=None) -> float | None`
  - `apps.status.services.get_average_latency_ms(days=90, as_of=None) -> int | None`
  - `apps.status.services.get_open_component_count(as_of=None) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_services.py
import datetime
from django.test import TestCase
from django.utils import timezone
from apps.status.models import ServiceComponent, ComponentHeartbeat, DailyComponentStatus
from apps.status.services import (
    record_heartbeats, rollup_daily_status, get_component_status,
    get_component_bars, get_uptime_percentage, get_average_latency_ms,
    get_open_component_count,
)


class RecordHeartbeatsTests(TestCase):
    def setUp(self):
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2')

    def test_creates_one_heartbeat_per_component(self):
        created = record_heartbeats()
        self.assertEqual(created, 2)
        self.assertEqual(ComponentHeartbeat.objects.filter(component=self.c1).count(), 1)
        self.assertEqual(ComponentHeartbeat.objects.filter(component=self.c2).count(), 1)

    def test_manual_down_override_marks_heartbeat_down(self):
        self.c1.manual_status = ServiceComponent.STATUS_DOWN
        self.c1.save()
        record_heartbeats()
        hb = ComponentHeartbeat.objects.get(component=self.c1)
        self.assertFalse(hb.is_up)


class RollupDailyStatusTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.today = timezone.localdate()

    def test_all_up_heartbeats_roll_up_operational(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)

    def test_any_down_heartbeat_rolls_up_down(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=False, latency_ms=None)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DOWN)

    def test_manual_status_overrides_heartbeats(self):
        self.component.manual_status = ServiceComponent.STATUS_DEGRADED
        self.component.save()
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_DEGRADED)

    def test_rollup_is_idempotent(self):
        ComponentHeartbeat.objects.create(component=self.component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        rollup_daily_status(self.today)
        rollup_daily_status(self.today)
        self.assertEqual(DailyComponentStatus.objects.filter(component=self.component, date=self.today).count(), 1)

    def test_no_heartbeats_defaults_operational(self):
        rollup_daily_status(self.today)
        daily = DailyComponentStatus.objects.get(component=self.component, date=self.today)
        self.assertEqual(daily.status, ServiceComponent.STATUS_OPERATIONAL)


class ComponentStatusQueryTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.today = timezone.localdate()

    def test_manual_status_takes_precedence(self):
        self.component.manual_status = ServiceComponent.STATUS_DOWN
        self.component.save()
        DailyComponentStatus.objects.create(component=self.component, date=self.today, status=ServiceComponent.STATUS_OPERATIONAL)
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_DOWN)

    def test_falls_back_to_daily_status(self):
        DailyComponentStatus.objects.create(component=self.component, date=self.today, status=ServiceComponent.STATUS_DEGRADED)
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_DEGRADED)

    def test_defaults_operational_with_no_data(self):
        self.assertEqual(get_component_status(self.component, self.today), ServiceComponent.STATUS_OPERATIONAL)

    def test_open_component_count(self):
        c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2', manual_status=ServiceComponent.STATUS_DEGRADED)
        self.assertEqual(get_open_component_count(self.today), 1)


class ComponentBarsTests(TestCase):
    def test_returns_30_bars_defaulting_operational_color(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        bars = get_component_bars(component, days=30, as_of=timezone.localdate())
        self.assertEqual(len(bars), 30)
        self.assertEqual(bars[-1]['color'], '#0E7A4F')

    def test_reflects_recorded_daily_status(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_DOWN)
        bars = get_component_bars(component, days=30, as_of=today)
        self.assertEqual(bars[-1]['color'], '#B3261E')


class MetricsTests(TestCase):
    def test_uptime_percentage_none_with_no_data(self):
        self.assertIsNone(get_uptime_percentage(days=90, as_of=timezone.localdate()))

    def test_uptime_percentage_computed(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        today = timezone.localdate()
        DailyComponentStatus.objects.create(component=component, date=today, status=ServiceComponent.STATUS_OPERATIONAL)
        DailyComponentStatus.objects.create(component=component, date=today - datetime.timedelta(days=1), status=ServiceComponent.STATUS_DOWN)
        pct = get_uptime_percentage(days=90, as_of=today)
        self.assertEqual(pct, 50.0)

    def test_average_latency_none_with_no_data(self):
        self.assertIsNone(get_average_latency_ms(days=90, as_of=timezone.now()))

    def test_average_latency_computed(self):
        component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        ComponentHeartbeat.objects.create(component=component, checked_at=timezone.now(), is_up=True, latency_ms=100)
        ComponentHeartbeat.objects.create(component=component, checked_at=timezone.now(), is_up=True, latency_ms=200)
        self.assertEqual(get_average_latency_ms(days=90, as_of=timezone.now()), 150)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_services -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.status.services'` (and `ComponentHeartbeat`/`DailyComponentStatus` don't exist yet).

- [ ] **Step 3: Implement models + services**

Append to `apps/status/models.py`:

```python
class ComponentHeartbeat(models.Model):
    component = models.ForeignKey(ServiceComponent, on_delete=models.CASCADE, related_name='heartbeats')
    checked_at = models.DateTimeField(db_index=True)
    is_up = models.BooleanField()
    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = 'status_component_heartbeats'
        indexes = [models.Index(fields=['component', 'checked_at'])]
        verbose_name = 'Denyut Nadi Komponen'
        verbose_name_plural = 'Daftar Denyut Nadi Komponen'

    def __str__(self):
        return f"{self.component.key} @ {self.checked_at} ({'up' if self.is_up else 'down'})"


class DailyComponentStatus(models.Model):
    component = models.ForeignKey(ServiceComponent, on_delete=models.CASCADE, related_name='daily_statuses')
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=ServiceComponent.STATUS_CHOICES)

    class Meta:
        db_table = 'status_daily_component_status'
        constraints = [
            models.UniqueConstraint(fields=['component', 'date'], name='unique_component_date')
        ]
        indexes = [models.Index(fields=['date'])]
        verbose_name = 'Status Harian Komponen'
        verbose_name_plural = 'Daftar Status Harian Komponen'

    def __str__(self):
        return f"{self.component.key} {self.date} = {self.status}"
```

Append to `apps/status/admin.py`:

```python
from .models import ComponentHeartbeat, DailyComponentStatus


@admin.register(ComponentHeartbeat)
class ComponentHeartbeatAdmin(admin.ModelAdmin):
    list_display = ('component', 'checked_at', 'is_up', 'latency_ms')
    list_filter = ('component', 'is_up')


@admin.register(DailyComponentStatus)
class DailyComponentStatusAdmin(admin.ModelAdmin):
    list_display = ('component', 'date', 'status')
    list_filter = ('component', 'status')
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations apps.status`

`apps/status/services.py`:

```python
"""Domain services for apps.status: heartbeat recording, rollup, and metric queries."""
import datetime
import time

from django.db import connection
from django.db.models import Avg
from django.utils import timezone

from .models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent

BAR_COLORS = {
    ServiceComponent.STATUS_OPERATIONAL: '#0E7A4F',
    ServiceComponent.STATUS_DEGRADED: '#B56A00',
    ServiceComponent.STATUS_DOWN: '#B3261E',
}


def _probe_database():
    """Lightweight DB connectivity + round-trip latency probe."""
    start = time.monotonic()
    is_up = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        is_up = False
    latency_ms = int((time.monotonic() - start) * 1000)
    return is_up, latency_ms


def record_heartbeats():
    """Probe the platform once and write one ComponentHeartbeat per ServiceComponent.

    A manual_status=DOWN override forces that component's heartbeat down
    regardless of the shared DB probe result; other overrides don't affect
    the heartbeat itself (they're applied at rollup/read time instead).
    """
    is_up, latency_ms = _probe_database()
    checked_at = timezone.now()
    created = 0
    for component in ServiceComponent.objects.all():
        component_is_up = False if component.manual_status == ServiceComponent.STATUS_DOWN else is_up
        ComponentHeartbeat.objects.create(
            component=component, checked_at=checked_at, is_up=component_is_up, latency_ms=latency_ms,
        )
        created += 1
    return created


def rollup_daily_status(date=None):
    """Upsert today's (or `date`'s) DailyComponentStatus per component from its heartbeats.

    Idempotent — safe to call repeatedly within the same day. manual_status
    always wins; otherwise DOWN if any heartbeat that day was down, else
    OPERATIONAL (no automatic DEGRADED signal exists without a manual override).
    """
    date = date or timezone.localdate()
    day_start = timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))
    day_end = timezone.make_aware(datetime.datetime.combine(date, datetime.time.max))
    updated = 0
    for component in ServiceComponent.objects.all():
        if component.manual_status:
            status = component.manual_status
        else:
            heartbeats = ComponentHeartbeat.objects.filter(
                component=component, checked_at__gte=day_start, checked_at__lte=day_end,
            )
            status = ServiceComponent.STATUS_DOWN if heartbeats.filter(is_up=False).exists() else ServiceComponent.STATUS_OPERATIONAL
        DailyComponentStatus.objects.update_or_create(
            component=component, date=date, defaults={'status': status},
        )
        updated += 1
    return updated


def get_component_status(component, date=None):
    """Current status for one component: manual override, else today's rollup, else OPERATIONAL default."""
    if component.manual_status:
        return component.manual_status
    date = date or timezone.localdate()
    daily = DailyComponentStatus.objects.filter(component=component, date=date).first()
    return daily.status if daily else ServiceComponent.STATUS_OPERATIONAL


def get_component_bars(component, days=30, as_of=None):
    """List of {'date': date, 'color': '#hex'} for the last `days`, oldest first."""
    as_of = as_of or timezone.localdate()
    start = as_of - datetime.timedelta(days=days - 1)
    statuses = {
        row.date: row.status
        for row in DailyComponentStatus.objects.filter(component=component, date__gte=start, date__lte=as_of)
    }
    bars = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        status = statuses.get(day, ServiceComponent.STATUS_OPERATIONAL)
        bars.append({'date': day, 'color': BAR_COLORS[status]})
    return bars


def get_uptime_percentage(days=90, as_of=None):
    """% of component-days OPERATIONAL in the last `days`, or None if there's no data yet."""
    as_of = as_of or timezone.localdate()
    start = as_of - datetime.timedelta(days=days - 1)
    qs = DailyComponentStatus.objects.filter(date__gte=start, date__lte=as_of)
    total = qs.count()
    if total == 0:
        return None
    operational = qs.filter(status=ServiceComponent.STATUS_OPERATIONAL).count()
    return round((operational / total) * 100, 2)


def get_average_latency_ms(days=90, as_of=None):
    """Average heartbeat latency_ms in the last `days`, or None if there's no data yet."""
    as_of = as_of or timezone.now()
    start = as_of - datetime.timedelta(days=days)
    agg = ComponentHeartbeat.objects.filter(
        checked_at__gte=start, checked_at__lte=as_of, latency_ms__isnull=False,
    ).aggregate(avg=Avg('latency_ms'))
    return round(agg['avg']) if agg['avg'] is not None else None


def get_open_component_count(as_of=None):
    """Count of components whose current status is not OPERATIONAL — the "open incidents" headline metric."""
    as_of = as_of or timezone.localdate()
    return sum(
        1 for component in ServiceComponent.objects.all()
        if get_component_status(component, as_of) != ServiceComponent.STATUS_OPERATIONAL
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_services -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/models.py apps/status/admin.py apps/status/services.py apps/status/migrations/ apps/status/tests/test_services.py
git commit -m "feat(status): add heartbeat/rollup models and metric query services"
```

---

### Task 5: `check_service_health` cron command

**Files:**
- Create: `apps/status/management/commands/check_service_health.py`
- Modify: `deploy/crontab` (append one line)
- Test: `apps/status/tests/test_check_service_health.py`

**Interfaces:**
- Consumes: `apps.status.services.record_heartbeats`, `apps.status.services.rollup_daily_status` (Task 4); `apps.core.locks.advisory_lock`; `apps.core.management.base.CronHostCommand`; `apps.core.models.JobRun`.

- [ ] **Step 1: Write the failing test**

```python
# apps/status/tests/test_check_service_health.py
import io
from django.core.management import call_command
from django.test import TestCase, override_settings
from apps.core.locks import advisory_lock
from apps.core.models import JobRun
from apps.status.models import ComponentHeartbeat, DailyComponentStatus, ServiceComponent


@override_settings(EDUCORE_CRON_HOST_ENFORCED=False)
class CheckServiceHealthCommandTests(TestCase):
    def setUp(self):
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2')

    def test_command_writes_heartbeats_and_rollup_and_job_run(self):
        out = io.StringIO()
        call_command('check_service_health', stdout=out)
        self.assertEqual(ComponentHeartbeat.objects.count(), 2)
        self.assertEqual(DailyComponentStatus.objects.count(), 2)
        job_run = JobRun.objects.filter(job_name='check_service_health').latest('id')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 2)

    def test_command_skips_when_lock_held(self):
        out = io.StringIO()
        with advisory_lock('check_service_health', timeout=0):
            call_command('check_service_health', stdout=out)
        self.assertEqual(ComponentHeartbeat.objects.count(), 0)
        self.assertIn('already held', out.getvalue())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_check_service_health -v 2`
Expected: FAIL — `CommandError: Unknown command: 'check_service_health'`

- [ ] **Step 3: Implement the command**

`apps/status/management/commands/check_service_health.py`:

```python
"""Cron: probe platform health and roll up today's per-component status (*/5, deploy/crontab)."""
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun
from apps.status.services import record_heartbeats, rollup_daily_status


class Command(CronHostCommand):
    help = "Probe platform health (DB connectivity) and roll up today's component status."

    def handle(self, *args, **options):
        with advisory_lock('check_service_health', timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    "Advisory lock 'check_service_health' already held. Exiting."
                ))
                return

            job_run = JobRun.objects.create(job_name='check_service_health', status=JobRun.STATUS_RUNNING)
            try:
                heartbeats_created = record_heartbeats()
                components_rolled_up = rollup_daily_status()
                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = heartbeats_created
                job_run.finished_at = timezone.now()
                job_run.save(update_fields=['status', 'items_processed', 'finished_at'])
                self.stdout.write(self.style.SUCCESS(
                    f"check_service_health: {heartbeats_created} heartbeats, {components_rolled_up} daily rollups"
                ))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)
                job_run.finished_at = timezone.now()
                job_run.save(update_fields=['status', 'error_text', 'finished_at'])
                raise
```

Append to `deploy/crontab` (matching the file's existing `*/N * * * *  sleep <N>; python /app/manage.py <command>  # comment` style and the `check_device_health` `*/10` cadence precedent — use `*/5` since this is a lighter single-probe check):

```
*/5 * * * *  sleep 8; python /app/manage.py check_service_health  # Service status page: DB probe + daily rollup
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_check_service_health -v 2`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/management/ deploy/crontab apps/status/tests/test_check_service_health.py
git commit -m "feat(status): add check_service_health cron command"
```

---

### Task 6: `StatusIncident` model + audited create/update services

**Files:**
- Modify: `apps/status/models.py` (append)
- Modify: `apps/status/admin.py` (register)
- Modify: `apps/status/services.py` (append `create_incident`/`update_incident`/`list_published_incidents`)
- Test: `apps/status/tests/test_incidents.py`

**Interfaces:**
- Consumes: `apps.core.services.audit(action, entity_type, entity_id, actor_id=None, role='', foundation_id=None, ...)` (existing, `apps/core/services.py:128`); `apps.identity.models.User`.
- Produces:
  - `apps.status.models.StatusIncident` with `SEVERITY_MINOR`/`SEVERITY_MAJOR`/`SEVERITY_MAINTENANCE`, fields `severity`, `title_id`, `title_en`, `body_id`, `body_en`, `occurred_at`, `duration_minutes`, `affected_components` (M2M), `published`, `created_by`, `updated_by`.
  - `apps.status.services.create_incident(*, severity, title_id, title_en, body_id, body_en, occurred_at, duration_minutes, affected_component_ids, published, actor) -> StatusIncident`
  - `apps.status.services.update_incident(incident, *, actor, **fields) -> StatusIncident`
  - `apps.status.services.list_published_incidents() -> QuerySet[StatusIncident]` (newest first)

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_incidents.py
from django.test import TestCase
from django.utils import timezone
from apps.core.models import AuditEvent
from apps.identity.models import User
from apps.status.models import ServiceComponent, StatusIncident
from apps.status.services import create_incident, update_incident, list_published_incidents


class IncidentServiceTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create(phone_e164='+6281200000005', full_name='Ops Actor')
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')

    def test_create_incident_writes_audit_event(self):
        incident = create_incident(
            severity=StatusIncident.SEVERITY_MINOR,
            title_id='Judul', title_en='Title',
            body_id='Isi', body_en='Body',
            occurred_at=timezone.now(), duration_minutes=30,
            affected_component_ids=[self.component.id], published=True, actor=self.actor,
        )
        self.assertEqual(incident.created_by, self.actor)
        self.assertIn(self.component, incident.affected_components.all())
        event = AuditEvent.objects.get(action='status.incident.created', entity_id=str(incident.id))
        self.assertEqual(event.actor_id, str(self.actor.id))

    def test_update_incident_writes_audit_event_and_sets_updated_by(self):
        incident = create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='A', title_en='A',
            body_id='A', body_en='A', occurred_at=timezone.now(), duration_minutes=10,
            affected_component_ids=[], published=False, actor=self.actor,
        )
        other_actor = User.objects.create(phone_e164='+6281200000006', full_name='Editor')
        updated = update_incident(incident, actor=other_actor, published=True)
        self.assertTrue(updated.published)
        self.assertEqual(updated.updated_by, other_actor)
        self.assertTrue(AuditEvent.objects.filter(action='status.incident.updated', entity_id=str(incident.id)).exists())

    def test_list_published_incidents_excludes_unpublished_and_orders_newest_first(self):
        old = create_incident(
            severity=StatusIncident.SEVERITY_MAINTENANCE, title_id='Old', title_en='Old',
            body_id='Old', body_en='Old', occurred_at=timezone.now() - timezone.timedelta(days=5),
            duration_minutes=5, affected_component_ids=[], published=True, actor=self.actor,
        )
        new = create_incident(
            severity=StatusIncident.SEVERITY_MAJOR, title_id='New', title_en='New',
            body_id='New', body_en='New', occurred_at=timezone.now(),
            duration_minutes=5, affected_component_ids=[], published=True, actor=self.actor,
        )
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Draft', title_en='Draft',
            body_id='Draft', body_en='Draft', occurred_at=timezone.now(),
            duration_minutes=5, affected_component_ids=[], published=False, actor=self.actor,
        )
        result = list(list_published_incidents())
        self.assertEqual(result, [new, old])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_incidents -v 2`
Expected: FAIL — `ImportError: cannot import name 'StatusIncident'`

- [ ] **Step 3: Implement**

Append to `apps/status/models.py`:

```python
class StatusIncident(models.Model):
    SEVERITY_MINOR = 'MINOR'
    SEVERITY_MAJOR = 'MAJOR'
    SEVERITY_MAINTENANCE = 'MAINTENANCE'
    SEVERITY_CHOICES = [
        (SEVERITY_MINOR, 'Minor'),
        (SEVERITY_MAJOR, 'Major'),
        (SEVERITY_MAINTENANCE, 'Maintenance'),
    ]

    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    title_id = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200)
    body_id = models.TextField()
    body_en = models.TextField()
    occurred_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField()
    affected_components = models.ManyToManyField(ServiceComponent, related_name='incidents', blank=True)
    published = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'status_incidents'
        ordering = ['-occurred_at']
        verbose_name = 'Insiden Status'
        verbose_name_plural = 'Daftar Insiden Status'

    def __str__(self):
        return f"[{self.severity}] {self.title_id}"
```

Append to `apps/status/admin.py`:

```python
from .models import StatusIncident


@admin.register(StatusIncident)
class StatusIncidentAdmin(admin.ModelAdmin):
    list_display = ('title_id', 'severity', 'occurred_at', 'published')
    list_filter = ('severity', 'published')
    filter_horizontal = ('affected_components',)
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations apps.status`

Append to `apps/status/services.py` (add import at top: `from apps.core.services import audit`, and `from .models import StatusIncident` alongside the existing model imports):

```python
def create_incident(*, severity, title_id, title_en, body_id, body_en, occurred_at,
                     duration_minutes, affected_component_ids, published, actor):
    """Create a StatusIncident and write an audit event (AGENTS.md red line #4)."""
    incident = StatusIncident.objects.create(
        severity=severity, title_id=title_id, title_en=title_en,
        body_id=body_id, body_en=body_en, occurred_at=occurred_at,
        duration_minutes=duration_minutes, published=published,
        created_by=actor, updated_by=actor,
    )
    incident.affected_components.set(affected_component_ids)
    audit(
        action='status.incident.created', entity_type='StatusIncident', entity_id=str(incident.id),
        actor_id=str(actor.id) if actor else None, role='platform_operator',
    )
    return incident


def update_incident(incident, *, actor, **fields):
    """Update a StatusIncident's fields and write an audit event."""
    for key, value in fields.items():
        setattr(incident, key, value)
    incident.updated_by = actor
    incident.save()
    audit(
        action='status.incident.updated', entity_type='StatusIncident', entity_id=str(incident.id),
        actor_id=str(actor.id) if actor else None, role='platform_operator',
    )
    return incident


def list_published_incidents():
    """Published incidents, newest occurred_at first (StatusIncident.Meta.ordering)."""
    return StatusIncident.objects.filter(published=True).prefetch_related('affected_components')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_incidents -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/models.py apps/status/admin.py apps/status/services.py apps/status/migrations/ apps/status/tests/test_incidents.py
git commit -m "feat(status): add StatusIncident model with audited create/update services"
```

---

### Task 7: `StatusSubscriber` model + subscribe service

**Files:**
- Modify: `apps/status/models.py` (append)
- Modify: `apps/status/admin.py` (register)
- Modify: `apps/status/services.py` (append `subscribe_email`)
- Test: `apps/status/tests/test_subscribers.py`

**Interfaces:**
- Produces: `apps.status.models.StatusSubscriber(email, subscribed_at, unsubscribe_token)`; `apps.status.services.subscribe_email(email: str) -> StatusSubscriber` (idempotent).

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_subscribers.py
from django.test import TestCase
from apps.status.models import StatusSubscriber
from apps.status.services import subscribe_email


class SubscribeEmailTests(TestCase):
    def test_new_email_creates_subscriber(self):
        subscriber = subscribe_email('parent@example.com')
        self.assertEqual(StatusSubscriber.objects.count(), 1)
        self.assertIsNotNone(subscriber.unsubscribe_token)

    def test_duplicate_email_is_idempotent(self):
        first = subscribe_email('parent@example.com')
        second = subscribe_email('parent@example.com')
        self.assertEqual(StatusSubscriber.objects.count(), 1)
        self.assertEqual(first.id, second.id)

    def test_unsubscribe_tokens_are_unique_across_subscribers(self):
        a = subscribe_email('a@example.com')
        b = subscribe_email('b@example.com')
        self.assertNotEqual(a.unsubscribe_token, b.unsubscribe_token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_subscribers -v 2`
Expected: FAIL — `ImportError: cannot import name 'StatusSubscriber'`

- [ ] **Step 3: Implement**

Append to `apps/status/models.py` (add `import uuid` at the top of the file):

```python
class StatusSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        db_table = 'status_subscribers'
        verbose_name = 'Pelanggan Pembaruan Status'
        verbose_name_plural = 'Daftar Pelanggan Pembaruan Status'

    def __str__(self):
        return self.email
```

Append to `apps/status/admin.py`:

```python
from .models import StatusSubscriber


@admin.register(StatusSubscriber)
class StatusSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'subscribed_at')
    search_fields = ('email',)
```

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations apps.status`

Append to `apps/status/services.py` (add `from .models import StatusSubscriber` to the model imports):

```python
def subscribe_email(email):
    """Idempotently capture an email for incident-update notifications (no delivery built yet)."""
    subscriber, _created = StatusSubscriber.objects.get_or_create(email=email)
    return subscriber
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_subscribers -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/models.py apps/status/admin.py apps/status/services.py apps/status/migrations/ apps/status/tests/test_subscribers.py
git commit -m "feat(status): add StatusSubscriber model and idempotent subscribe service"
```

---

### Task 8: `id-ID` first status page strings module

**Files:**
- Create: `apps/status/strings.py`
- Test: `apps/status/tests/test_strings.py`

**Interfaces:**
- Produces: `apps.status.strings.STATUS_STRINGS: dict[str, dict[str, str]]` keyed `'ID'`/`'EN'`, each a flat dict of the `st_*`/`sev_*` keys used below; `apps.status.strings.get_status_strings(lang_code: str) -> dict[str, str]` (defaults to `'ID'` for any unrecognized code — id-ID first).

- [ ] **Step 1: Write the failing test**

```python
# apps/status/tests/test_strings.py
from django.test import TestCase
from apps.status.strings import STATUS_STRINGS, get_status_strings


class StatusStringsTests(TestCase):
    def test_both_locales_present(self):
        self.assertIn('ID', STATUS_STRINGS)
        self.assertIn('EN', STATUS_STRINGS)

    def test_id_and_en_have_identical_key_sets(self):
        self.assertEqual(set(STATUS_STRINGS['ID'].keys()), set(STATUS_STRINGS['EN'].keys()))

    def test_get_status_strings_defaults_to_id(self):
        self.assertEqual(get_status_strings('XX'), STATUS_STRINGS['ID'])
        self.assertEqual(get_status_strings(None), STATUS_STRINGS['ID'])

    def test_get_status_strings_returns_english(self):
        strings = get_status_strings('EN')
        self.assertEqual(strings['st_banner'], 'All systems operational')

    def test_indonesian_banner_copy(self):
        self.assertEqual(STATUS_STRINGS['ID']['st_banner'], 'Semua sistem beroperasi normal')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_strings -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.status.strings'`

- [ ] **Step 3: Implement**

`apps/status/strings.py` — copy verbatim from the source design mockup (Global Constraints: do not re-translate):

```python
"""id-ID first status-page copy, lifted verbatim from the source design mockup
(docs/superpowers/specs/2026-09-18-service-status-page-design.md)."""

STATUS_STRINGS = {
    'ID': {
        'st_eyebrow': 'Status layanan',
        'st_h1': 'Status langsung setiap layanan EduCore.',
        'st_banner': 'Semua sistem beroperasi normal',
        'st_banner_degraded': 'Sebagian sistem mengalami gangguan',
        'st_m1': 'uptime 90 hari terakhir',
        'st_m2': 'waktu respons API rata-rata',
        'st_m3': 'insiden terbuka',
        'st_comp_h': 'Komponen',
        'st_legend': '30 hari terakhir',
        'st_ok': 'Operasional',
        'st_degraded': 'Menurun',
        'st_down': 'Gangguan',
        'st_window': 'Jendela pemeliharaan terjadwal: Minggu 01:00–03:00 WIB, di luar jam sekolah.',
        'st_inc_h': 'Riwayat insiden',
        'sev_minor': 'Minor',
        'sev_major': 'Mayor',
        'sev_maint': 'Pemeliharaan',
        'st_sub_note': 'Berlangganan pemberitahuan insiden lewat surel atau webhook — sama seperti kejadian domain lain.',
        'st_sub_cta': 'Berlangganan pembaruan',
        'st_sub_placeholder': 'Alamat surel Anda',
        'st_sub_success': 'Anda telah berlangganan pembaruan status.',
    },
    'EN': {
        'st_eyebrow': 'Service status',
        'st_h1': 'Live status for every EduCore service.',
        'st_banner': 'All systems operational',
        'st_banner_degraded': 'Some systems are experiencing issues',
        'st_m1': 'uptime, last 90 days',
        'st_m2': 'average API response time',
        'st_m3': 'open incidents',
        'st_comp_h': 'Components',
        'st_legend': 'Last 30 days',
        'st_ok': 'Operational',
        'st_degraded': 'Degraded',
        'st_down': 'Down',
        'st_window': 'Scheduled maintenance window: Sundays 01:00–03:00 WIB, outside school hours.',
        'st_inc_h': 'Incident history',
        'sev_minor': 'Minor',
        'sev_major': 'Major',
        'sev_maint': 'Maintenance',
        'st_sub_note': 'Subscribe to incident notifications by email or webhook — same as other domain events.',
        'st_sub_cta': 'Subscribe to updates',
        'st_sub_placeholder': 'Your email address',
        'st_sub_success': 'You are now subscribed to status updates.',
    },
}


def get_status_strings(lang_code):
    """id-ID first: any unrecognized/missing lang_code falls back to Indonesian."""
    return STATUS_STRINGS.get((lang_code or '').upper(), STATUS_STRINGS['ID'])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_strings -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/strings.py apps/status/tests/test_strings.py
git commit -m "feat(status): add id-ID first status page copy"
```

---

### Task 9: Public status page view + URL + template

**Files:**
- Create: `apps/status/views.py`
- Create: `apps/status/urls.py`
- Modify: `educore/urls.py` (add one `path(...)` line, mirroring the marketing include)
- Create: `frontend/templates/status/status.html`
- Test: `apps/status/tests/test_views.py`

**Interfaces:**
- Consumes: `apps.status.services.{get_uptime_percentage,get_average_latency_ms,get_open_component_count,get_component_bars,get_component_status,list_published_incidents}` (Tasks 4/6), `apps.status.strings.get_status_strings` (Task 8), `apps.status.models.ServiceComponent`.
- Produces: `apps.status.views.StatusPageView` (`TemplateView`), URL name `status:page` at `/status/`.

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_views.py
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.status.models import ServiceComponent, DailyComponentStatus, StatusIncident
from apps.status.services import create_incident
from apps.identity.models import User


class StatusPageViewTests(TestCase):
    def setUp(self):
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='Portal', name_en='Portal', display_order=1)
        self.actor = User.objects.create(phone_e164='+6281200000007', full_name='Ops')

    def test_page_renders_200(self):
        response = self.client.get(reverse('status:page'))
        self.assertEqual(response.status_code, 200)

    def test_page_shows_component_name(self):
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Portal')

    def test_banner_reflects_all_operational(self):
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Semua sistem beroperasi normal')

    def test_banner_reflects_degraded_component(self):
        self.c1.manual_status = ServiceComponent.STATUS_DEGRADED
        self.c1.save()
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Sebagian sistem mengalami gangguan')

    def test_published_incident_shown_unpublished_hidden(self):
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Kejadian tampil', title_en='Visible incident',
            body_id='x', body_en='x', occurred_at=timezone.now(), duration_minutes=5,
            affected_component_ids=[], published=True, actor=self.actor,
        )
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Kejadian sembunyi', title_en='Hidden incident',
            body_id='x', body_en='x', occurred_at=timezone.now(), duration_minutes=5,
            affected_component_ids=[], published=False, actor=self.actor,
        )
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Kejadian tampil')
        self.assertNotContains(response, 'Kejadian sembunyi')

    def test_english_locale_query_param(self):
        response = self.client.get(reverse('status:page'), {'lang': 'EN'})
        self.assertContains(response, 'All systems operational')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_views -v 2`
Expected: FAIL — `NoReverseMatch: 'status' is not a registered namespace`

- [ ] **Step 3: Implement**

`apps/status/views.py`:

```python
"""Public (no-auth, no-tenant) service status page — mirrors apps.marketing's TemplateView pattern."""
from django.views.generic import TemplateView

from .models import ServiceComponent
from .services import (
    get_average_latency_ms, get_component_bars, get_component_status,
    get_open_component_count, get_uptime_percentage, list_published_incidents,
)
from .strings import get_status_strings


class StatusPageView(TemplateView):
    template_name = 'status/status.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lang_code = self.request.GET.get('lang', 'ID')
        t = get_status_strings(lang_code)
        ctx['t'] = t
        ctx['lang_code'] = 'EN' if lang_code.upper() == 'EN' else 'ID'

        components = []
        any_degraded = False
        any_down = False
        for component in ServiceComponent.objects.all():
            status = get_component_status(component)
            if status == ServiceComponent.STATUS_DOWN:
                any_down = True
            elif status == ServiceComponent.STATUS_DEGRADED:
                any_degraded = True
            status_label = {
                ServiceComponent.STATUS_OPERATIONAL: t['st_ok'],
                ServiceComponent.STATUS_DEGRADED: t['st_degraded'],
                ServiceComponent.STATUS_DOWN: t['st_down'],
            }[status]
            status_dot = {
                ServiceComponent.STATUS_OPERATIONAL: '#0E7A4F',
                ServiceComponent.STATUS_DEGRADED: '#B56A00',
                ServiceComponent.STATUS_DOWN: '#B3261E',
            }[status]
            components.append({
                'name': component.name_en if ctx['lang_code'] == 'EN' else component.name_id,
                'note': component.note_en if ctx['lang_code'] == 'EN' else component.note_id,
                'status': status_label,
                'dot': status_dot,
                'bars': get_component_bars(component),
            })
        ctx['components'] = components
        ctx['banner_ok'] = not (any_degraded or any_down)
        ctx['banner_text'] = t['st_banner'] if ctx['banner_ok'] else t['st_banner_degraded']

        ctx['uptime_pct'] = get_uptime_percentage()
        ctx['avg_latency_ms'] = get_average_latency_ms()
        ctx['open_component_count'] = get_open_component_count()

        incidents = []
        for incident in list_published_incidents():
            incidents.append({
                'severity': t['sev_minor'] if incident.severity == 'MINOR' else t['sev_major'] if incident.severity == 'MAJOR' else t['sev_maint'],
                'title': incident.title_en if ctx['lang_code'] == 'EN' else incident.title_id,
                'body': incident.body_en if ctx['lang_code'] == 'EN' else incident.body_id,
                'occurred_at': incident.occurred_at,
                'duration_minutes': incident.duration_minutes,
                'affected': [
                    c.name_en if ctx['lang_code'] == 'EN' else c.name_id
                    for c in incident.affected_components.all()
                ],
            })
        ctx['incidents'] = incidents
        return ctx
```

`apps/status/urls.py`:

```python
"""URL routes for the public service status page (no auth, no /api/v1 prefix)."""
from django.urls import path

from . import views

app_name = 'status'

urlpatterns = [
    path('', views.StatusPageView.as_view(), name='page'),
]
```

In `educore/urls.py`, change:

```python
    # Public marketing website — no auth, no tenancy, root-mounted
    path('', include('apps.marketing.urls')),
]
```

to:

```python
    # Public marketing website — no auth, no tenancy, root-mounted
    path('', include('apps.marketing.urls')),
    # Public service status page — no auth, no tenancy, non-tenant apps.status models
    path('status/', include('apps.status.urls')),
]
```

`frontend/templates/status/status.html` (extends the shared marketing base for nav/footer, per the design spec):

```html
{% extends "marketing/base.html" %}

{% block title %}{{ t.st_h1 }}{% endblock %}

{% block content %}
<section style="background:#fff;border-bottom:1px solid #E5DDD9">
  <div style="max-width:1100px;margin:0 auto;padding:64px 24px 56px;display:flex;flex-direction:column;gap:26px">
    <div style="display:flex;flex-direction:column;gap:12px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#C8102E">{{ t.st_eyebrow }}</span>
      <h1 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:clamp(28px,4.6vw,42px);letter-spacing:-.03em">{{ t.st_h1 }}</h1>
    </div>
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:{% if banner_ok %}#E6F4EE{% else %}#FDF1E0{% endif %};border-left:3px solid {% if banner_ok %}#0E7A4F{% else %}#B56A00{% endif %};padding:20px 22px">
      <span style="width:12px;height:12px;border-radius:50%;background:{% if banner_ok %}#0E7A4F{% else %}#B56A00{% endif %};flex:none"></span>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:19px;color:{% if banner_ok %}#0B5B3B{% else %}#7A4400{% endif %}">{{ banner_text }}</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:20px">
      <div style="background:#F7F4F2;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:5px"><span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:28px">{% if uptime_pct is not None %}{{ uptime_pct }}%{% else %}—{% endif %}</span><span style="font-size:13.5px;color:#6B615C">{{ t.st_m1 }}</span></div>
      <div style="background:#F7F4F2;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:5px"><span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:28px">{% if avg_latency_ms is not None %}{{ avg_latency_ms }} ms{% else %}—{% endif %}</span><span style="font-size:13.5px;color:#6B615C">{{ t.st_m2 }}</span></div>
      <div style="background:#F7F4F2;border:1px solid #E5DDD9;padding:20px;display:flex;flex-direction:column;gap:5px"><span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:28px">{{ open_component_count }}</span><span style="font-size:13.5px;color:#6B615C">{{ t.st_m3 }}</span></div>
    </div>
  </div>
</section>

<section style="background:#F7F4F2;border-bottom:1px solid #E5DDD9">
  <div style="max-width:1100px;margin:0 auto;padding:56px 24px;display:flex;flex-direction:column;gap:22px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap">
      <h2 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:clamp(23px,3.2vw,28px);letter-spacing:-.02em">{{ t.st_comp_h }}</h2>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:#6B615C">{{ t.st_legend }}</span>
    </div>
    <div style="background:#fff;border:1px solid #E5DDD9;display:flex;flex-direction:column">
      {% for c in components %}
      <div style="padding:18px 20px;border-bottom:1px solid #E5DDD9;display:flex;flex-wrap:wrap;align-items:center;gap:14px">
        <span style="display:flex;flex-direction:column;gap:3px;flex:1 1 200px;min-width:0">
          <span style="font-size:15px;font-weight:600">{{ c.name }}</span>
          <span style="font-size:13px;color:#6B615C;line-height:1.5">{{ c.note }}</span>
        </span>
        <span style="display:flex;gap:2px;align-items:flex-end;height:26px;flex:none">
          {% for b in c.bars %}<span style="width:5px;height:26px;background:{{ b.color }};display:block"></span>{% endfor %}
        </span>
        <span style="display:flex;align-items:center;gap:8px;flex:none;min-width:116px;justify-content:flex-end">
          <span style="width:9px;height:9px;border-radius:50%;background:{{ c.dot }};display:block"></span>
          <span style="font-size:13.5px;font-weight:600;color:{{ c.dot }}">{{ c.status }}</span>
        </span>
      </div>
      {% endfor %}
    </div>
    <p style="margin:0;font-size:13.5px;line-height:1.6;color:#6B615C">{{ t.st_window }}</p>
  </div>
</section>

<section style="background:#fff">
  <div style="max-width:1100px;margin:0 auto;padding:56px 24px;display:flex;flex-direction:column;gap:24px">
    <h2 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:clamp(23px,3.2vw,28px);letter-spacing:-.02em">{{ t.st_inc_h }}</h2>
    <div style="display:flex;flex-direction:column;gap:0">
      {% for i in incidents %}
      <div style="display:grid;grid-template-columns:minmax(0,1fr);gap:10px;padding:24px 0;border-top:1px solid #E5DDD9">
        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px">
          <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.12em;text-transform:uppercase;font-weight:600;padding:4px 9px;background:#F7F4F2;color:#3A302C">{{ i.severity }}</span>
          <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:17px">{{ i.title }}</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#6B615C;margin-left:auto">{{ i.occurred_at|date:"d M Y" }} · {{ i.duration_minutes }} min</span>
        </div>
        <p style="margin:0;font-size:14.5px;line-height:1.65;color:#3A302C;max-width:82ch">{{ i.body }}</p>
        <div style="display:flex;flex-wrap:wrap;gap:8px">
          {% for a in i.affected %}<span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;background:#F7F4F2;border:1px solid #E5DDD9;padding:5px 9px;color:#3A302C">{{ a }}</span>{% endfor %}
        </div>
      </div>
      {% endfor %}
    </div>
    <div style="border-top:1px solid #E5DDD9;padding-top:24px;display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between">
      <p style="margin:0;font-size:14px;color:#6B615C;max-width:56ch;line-height:1.6">{{ t.st_sub_note }}</p>
      <form method="post" action="{% url 'status:subscribe' %}" style="display:flex;gap:8px;flex-wrap:wrap">
        {% csrf_token %}
        <input type="email" name="email" required placeholder="{{ t.st_sub_placeholder }}" style="font-family:'IBM Plex Sans',sans-serif;font-size:14px;padding:12px 14px;border:1px solid #E5DDD9">
        <button type="submit" style="font-family:'IBM Plex Sans',sans-serif;font-size:14px;font-weight:600;padding:13px 20px;border:1px solid #16110F;background:#fff;color:#16110F;cursor:pointer;white-space:nowrap">{{ t.st_sub_cta }}</button>
      </form>
    </div>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_views -v 2`
Expected: all pass. (Task 10 adds the `status:subscribe` URL the template references — the template renders fine before that since Django template `{% url %}` resolution happens at render time and the tag will only fail if that specific form-render path is hit; the view tests above assert on banner/component/incident text, not the form action, so this ordering is safe. If any test does inadvertently hit `{% url 'status:subscribe' %}` before Task 10, stub the URL in this task's `urls.py` with `path('subscribe/', views.StatusPageView.as_view(), name='subscribe')` temporarily and let Task 10 replace it — prefer instead to just do Task 10 immediately after this one before running the full suite.)

- [ ] **Step 5: Commit**

```bash
git add apps/status/views.py apps/status/urls.py educore/urls.py frontend/templates/status/ apps/status/tests/test_views.py
git commit -m "feat(status): add public status page view, URL, and template"
```

---

### Task 10: Subscribe endpoint

**Files:**
- Modify: `apps/status/views.py` (append `StatusSubscribeView`)
- Modify: `apps/status/urls.py` (add `subscribe/` path)
- Test: `apps/status/tests/test_subscribe_view.py`

**Interfaces:**
- Consumes: `apps.status.services.subscribe_email` (Task 7).
- Produces: `apps.status.views.StatusSubscribeView`, URL name `status:subscribe` at `/status/subscribe/`.

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_subscribe_view.py
from django.test import TestCase
from django.urls import reverse
from apps.status.models import StatusSubscriber


class StatusSubscribeViewTests(TestCase):
    def test_post_valid_email_creates_subscriber_and_redirects(self):
        response = self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusSubscriber.objects.count(), 1)

    def test_post_duplicate_email_does_not_error(self):
        self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        response = self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusSubscriber.objects.count(), 1)

    def test_post_missing_email_returns_400(self):
        response = self.client.post(reverse('status:subscribe'), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusSubscriber.objects.count(), 0)

    def test_get_not_allowed(self):
        response = self.client.get(reverse('status:subscribe'))
        self.assertEqual(response.status_code, 405)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_subscribe_view -v 2`
Expected: FAIL — `NoReverseMatch: 'subscribe' is not a registered namespace`

- [ ] **Step 3: Implement**

Append to `apps/status/views.py` (add `from django.http import HttpResponseBadRequest, HttpResponseRedirect` and `from django.views import View` and `from django.urls import reverse` to the imports, plus `from .services import subscribe_email` alongside the existing services import):

```python
class StatusSubscribeView(View):
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        email = request.POST.get('email', '').strip()
        if not email:
            return HttpResponseBadRequest('email is required')
        subscribe_email(email)
        return HttpResponseRedirect(reverse('status:page'))
```

In `apps/status/urls.py`, add:

```python
urlpatterns = [
    path('', views.StatusPageView.as_view(), name='page'),
    path('subscribe/', views.StatusSubscribeView.as_view(), name='subscribe'),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_subscribe_view apps.status.tests.test_views -v 2`
Expected: all pass (re-run `test_views` too, confirming the template's `{% url 'status:subscribe' %}` now resolves cleanly).

- [ ] **Step 5: Commit**

```bash
git add apps/status/views.py apps/status/urls.py apps/status/tests/test_subscribe_view.py
git commit -m "feat(status): add public subscribe endpoint"
```

---

### Task 11: Internal management views (component override + incident CRUD)

**Files:**
- Create: `apps/status/web_views.py`
- Create: `apps/status/web_urls.py`
- Modify: `educore/urls.py` (add one `path(...)` line for the web management routes)
- Create: `frontend/templates/status/manage.html`
- Test: `apps/status/tests/test_manage_views.py`

**Interfaces:**
- Consumes: `apps.identity.permissions.HasRequiredPermission` (Task 2's platform-permission hook), `apps.status.services.{update_incident,create_incident}` (Task 6), `ServiceComponent` (Task 3).
- Produces: `apps.status.web_views.StatusManagePageView`, `StatusComponentUpdateView`, `StatusIncidentCreateView`, `StatusIncidentUpdateView`, all requiring `status.write`. URL names `status_manage:page`, `status_manage:component-update`, `status_manage:incident-create`, `status_manage:incident-update`, mounted at `/web/status/manage/...`.

- [ ] **Step 1: Write the failing tests**

```python
# apps/status/tests/test_manage_views.py
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.identity.models import PlatformRoleAssignment, User
from apps.status.models import ServiceComponent, StatusIncident


class StatusManageAccessTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.operator = User.objects.create(phone_e164='+6281200000008', full_name='Op', is_active=True)
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.plain_user = User.objects.create(phone_e164='+6281200000009', full_name='Plain', is_active=True)
        self.plain_user.set_password('pw12345')
        self.plain_user.save()

    def test_anonymous_denied(self):
        response = self.client.get(reverse('status_manage:page'))
        self.assertIn(response.status_code, (401, 403))

    def test_non_operator_denied(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse('status_manage:page'))
        self.assertEqual(response.status_code, 403)

    def test_operator_allowed(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse('status_manage:page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'C1')


class StatusComponentUpdateViewTests(TestCase):
    def setUp(self):
        self.component = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1')
        self.operator = User.objects.create(phone_e164='+6281200000010', full_name='Op')
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.client.force_login(self.operator)

    def test_set_manual_status(self):
        url = reverse('status_manage:component-update', args=[self.component.id])
        response = self.client.post(url, {'manual_status': ServiceComponent.STATUS_DEGRADED})
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertEqual(self.component.manual_status, ServiceComponent.STATUS_DEGRADED)

    def test_clear_manual_status(self):
        self.component.manual_status = ServiceComponent.STATUS_DOWN
        self.component.save()
        url = reverse('status_manage:component-update', args=[self.component.id])
        response = self.client.post(url, {'manual_status': ''})
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertIsNone(self.component.manual_status)


class StatusIncidentManageViewTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create(phone_e164='+6281200000011', full_name='Op')
        self.operator.set_password('pw12345')
        self.operator.save()
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.client.force_login(self.operator)

    def test_create_incident(self):
        url = reverse('status_manage:incident-create')
        response = self.client.post(url, {
            'severity': StatusIncident.SEVERITY_MINOR,
            'title_id': 'Judul', 'title_en': 'Title',
            'body_id': 'Isi', 'body_en': 'Body',
            'duration_minutes': '15',
            'published': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusIncident.objects.count(), 1)
        incident = StatusIncident.objects.first()
        self.assertEqual(incident.created_by, self.operator)

    def test_toggle_publish(self):
        incident = StatusIncident.objects.create(
            severity=StatusIncident.SEVERITY_MINOR, title_id='A', title_en='A',
            body_id='A', body_en='A', occurred_at=timezone.now(), duration_minutes=5, published=False,
        )
        url = reverse('status_manage:incident-update', args=[incident.id])
        response = self.client.post(url, {'published': 'on'})
        self.assertEqual(response.status_code, 302)
        incident.refresh_from_db()
        self.assertTrue(incident.published)
        self.assertEqual(incident.updated_by, self.operator)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_manage_views -v 2`
Expected: FAIL — `NoReverseMatch: 'status_manage' is not a registered namespace`

- [ ] **Step 3: Implement**

`apps/status/web_views.py`:

```python
"""Session-auth internal management pages for apps.status — mirrors
apps.academic.views' PermissionSlipWebAccessMixin (APIView + render()) pattern.
Gated by the platform-wide 'status.write' permission (Task 2), not the
tenant-scoped RoleAssignment table.
"""
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from rest_framework.views import APIView

from apps.identity.permissions import HasRequiredPermission
from .models import ServiceComponent, StatusIncident
from .services import create_incident, update_incident


class StatusManageAccessMixin:
    """Shared gating: session-authenticated users holding the platform status.write permission."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'status.write'


class StatusManagePageView(StatusManageAccessMixin, APIView):
    """GET /web/status/manage/ — list components and incidents for editing."""

    def get(self, request):
        components = ServiceComponent.objects.all()
        incidents = StatusIncident.objects.all()
        return render(request, 'status/manage.html', {
            'components': components,
            'incidents': incidents,
        })


class StatusComponentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/components/<id>/ — set or clear a component's manual_status override."""

    def post(self, request, component_id):
        component = get_object_or_404(ServiceComponent, id=component_id)
        manual_status = request.POST.get('manual_status', '').strip()
        component.manual_status = manual_status or None
        component.save(update_fields=['manual_status'])
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentCreateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/create/ — create a new incident."""

    def post(self, request):
        affected_ids = request.POST.getlist('affected_components')
        create_incident(
            severity=request.POST['severity'],
            title_id=request.POST['title_id'], title_en=request.POST['title_en'],
            body_id=request.POST['body_id'], body_en=request.POST['body_en'],
            occurred_at=timezone.now(),
            duration_minutes=int(request.POST['duration_minutes']),
            affected_component_ids=[int(cid) for cid in affected_ids],
            published=bool(request.POST.get('published')),
            actor=request.user,
        )
        return HttpResponseRedirect(reverse('status_manage:page'))


class StatusIncidentUpdateView(StatusManageAccessMixin, APIView):
    """POST /web/status/manage/incidents/<id>/ — toggle publish or edit an existing incident."""

    def post(self, request, incident_id):
        incident = get_object_or_404(StatusIncident, id=incident_id)
        update_incident(incident, actor=request.user, published=bool(request.POST.get('published')))
        return HttpResponseRedirect(reverse('status_manage:page'))
```

`apps/status/web_urls.py`:

```python
"""Web (session-auth) URL routes for apps.status — mounted under /web/status/manage/."""
from django.urls import path

from . import web_views

app_name = 'status_manage'

urlpatterns = [
    path('manage/', web_views.StatusManagePageView.as_view(), name='page'),
    path('manage/components/<int:component_id>/', web_views.StatusComponentUpdateView.as_view(), name='component-update'),
    path('manage/incidents/create/', web_views.StatusIncidentCreateView.as_view(), name='incident-create'),
    path('manage/incidents/<int:incident_id>/', web_views.StatusIncidentUpdateView.as_view(), name='incident-update'),
]
```

In `educore/urls.py`, add (near the other `/web/` entries, before the `i18n/` line):

```python
    path('web/status/', include('apps.status.web_urls')),
```

`frontend/templates/status/manage.html` (minimal internal tool — no design-system polish needed, unlike the public page):

```html
<!DOCTYPE html>
<html lang="id">
<head><meta charset="utf-8"><title>Kelola Status Layanan</title></head>
<body>
  <h1>Kelola Status Layanan</h1>

  <h2>Komponen</h2>
  <table>
    <tr><th>Nama</th><th>Status manual</th><th></th></tr>
    {% for c in components %}
    <tr>
      <td>{{ c.name_id }}</td>
      <td>{{ c.manual_status|default:"(otomatis)" }}</td>
      <td>
        <form method="post" action="{% url 'status_manage:component-update' c.id %}">
          {% csrf_token %}
          <select name="manual_status">
            <option value="">(otomatis)</option>
            <option value="OPERATIONAL">Operasional</option>
            <option value="DEGRADED">Menurun</option>
            <option value="DOWN">Gangguan</option>
          </select>
          <button type="submit">Simpan</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>

  <h2>Insiden</h2>
  <table>
    <tr><th>Judul</th><th>Tingkat</th><th>Terbit</th></tr>
    {% for i in incidents %}
    <tr>
      <td>{{ i.title_id }}</td>
      <td>{{ i.severity }}</td>
      <td>{{ i.published }}</td>
    </tr>
    {% endfor %}
  </table>

  <h2>Buat insiden baru</h2>
  <form method="post" action="{% url 'status_manage:incident-create' %}">
    {% csrf_token %}
    <label>Tingkat <select name="severity">
      <option value="MINOR">Minor</option>
      <option value="MAJOR">Mayor</option>
      <option value="MAINTENANCE">Pemeliharaan</option>
    </select></label>
    <label>Judul (ID) <input type="text" name="title_id" required></label>
    <label>Title (EN) <input type="text" name="title_en" required></label>
    <label>Isi (ID) <textarea name="body_id" required></textarea></label>
    <label>Body (EN) <textarea name="body_en" required></textarea></label>
    <label>Durasi (menit) <input type="number" name="duration_minutes" required></label>
    <label>Terbitkan <input type="checkbox" name="published" checked></label>
    <button type="submit">Buat</button>
  </form>
</body>
</html>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status.tests.test_manage_views -v 2`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/status/web_views.py apps/status/web_urls.py educore/urls.py frontend/templates/status/manage.html apps/status/tests/test_manage_views.py
git commit -m "feat(status): add internal management views gated by platform status.write permission"
```

---

### Task 12: Final wiring check + full regression pass

**Files:**
- No new files. Verifies Tasks 1–11 are fully wired.

- [ ] **Step 1: System check**

Run: `EDUCORE_USE_SQLITE=1 python manage.py check`
Expected: `System check identified no issues.`

- [ ] **Step 2: Migrations check (no missing migrations)**

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations --check --dry-run`
Expected: exits 0, no output (all migrations already generated across Tasks 1, 3, 4, 6, 7).

- [ ] **Step 3: Full apps.status + apps.identity suite**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.status apps.identity -v 2`
Expected: all pass, no regressions in `apps.identity`'s existing RBAC/permission tests from Task 2's insertion.

- [ ] **Step 4: Full project suite**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test`
Expected: same pass/fail count as `main` before this branch (confirm any pre-existing unrelated failures via `git stash` + re-run on `main`, per this repo's established verification convention — see recent entries in `memory/01_PROJECT.md`).

- [ ] **Step 5: Manual smoke check**

Run: `EDUCORE_USE_SQLITE=1 python manage.py migrate && EDUCORE_USE_SQLITE=1 python manage.py runserver` and visit `http://localhost:8000/status/` — confirm the 6 seeded components render with green bars (no heartbeat data yet is fine; `get_component_status` defaults to `OPERATIONAL`). Stop the server.

- [ ] **Step 6: Commit (if anything changed)**

Only commit if Step 1–4 required a fix. Otherwise this task is verification-only and produces no diff.

```bash
git add -A
git commit -m "test(status): fix regressions found in full-suite verification pass"
```

# Service Status Page — Design Spec

Date: 2026-09-18
Status: Approved (pending write-up review)
Source: Design mockup at `https://claude.ai/artifact/Wa3PnszNrgEksj6ai6otCt` (screen "06 Service status" of the marketing multi-screen prototype). This spec implements that screen as a real, database-backed feature — the mockup itself is static/JS-mocked and not implemented as-is.

## 1. Problem & Scope

EduCore needs a public status page (`/status/`) showing live platform health: overall banner, three headline metrics (90-day uptime %, average API latency, open incident count), a per-component status grid with a 30-day history strip, and an incident history list. Visitors can also leave an email to be notified of incidents (capture only — no delivery mechanism yet).

This is new, platform-wide (non-tenant) functionality — it doesn't belong to any one foundation/school, unlike every other EduCore feature.

### Non-goals (this slice)
- Real per-provider health checks (Payments/Xendit, WhatsApp, POS terminals) — only DB + web process connectivity is actually probed. Non-DB components' status is staff-set (`manual_status`) or defaults to the shared system heartbeat.
- Subscriber email delivery — `StatusSubscriber` rows are captured only; no outbound email channel exists anywhere in this repo yet. Logged as a follow-up Open Item.
- A full PLATFORM-scope RBAC redesign — only the minimum needed for this feature (a `platform_operator` role, one permission key) is built. `RoleAssignment`/`TenantManager`/`TenancyMiddleware` are untouched.
- Multi-update incident timelines (investigating → identified → resolved). The mockup shows one body per incident; that's what's built.

## 2. App placement

New `apps.status` app, registered in `INSTALLED_APPS` (`educore/settings/base.py`) alongside the other EduCore apps. Chosen over folding into `apps.marketing` because it owns cron jobs, dedicated models, and the new platform-permission concern that don't belong to "marketing."

## 3. Data model (`apps/status/models.py`)

All models here are plain `models.Model`, **not** `TenantModel` — status data has no `foundation_id`, matching the existing precedent of `apps.core`'s `AuditEvent`/`DomainEvent`/`TaskQueue`/`JobRun`/`IdempotencyRecord` (all plain models) and `apps.identity.OTPChallenge`.

- **`ServiceComponent`**
  - `key` (slug, unique) — e.g. `web_portal`, `partner_api`, `mobile_apps`, `notifications`, `payments`, `canteen_pos`.
  - `name_id` / `name_en`, `note_id` / `note_en` — id-ID first, English secondary (AGENTS.md §6).
  - `display_order` (int).
  - `manual_status` (nullable choice: `OPERATIONAL` / `DEGRADED` / `DOWN`) — staff override; `null` means "derive from heartbeat."
  - Seeded via a data migration with the 6 components from the mockup.

- **`ComponentHeartbeat`**
  - `component` FK, `checked_at` (indexed), `is_up` (bool), `latency_ms` (int, nullable).
  - One row per component per cron run. Since only one real probe exists (DB + web), every component gets a heartbeat row with the same `is_up`/`latency_ms` per run, unless that component has a same-day `manual_status` override.

- **`DailyComponentStatus`**
  - `component` FK, `date`, `status` (`OPERATIONAL`/`DEGRADED`/`DOWN`), unique together `(component, date)`.
  - One row per component per day — rolled up from that day's heartbeats each cron run (idempotent upsert), or forced by `manual_status` if staff set one. Feeds the 30-day bar strip and the 90-day uptime % / latency metrics.

- **`StatusIncident`**
  - `severity` (`MINOR`/`MAJOR`/`MAINTENANCE`), `title_id`/`title_en`, `body_id`/`body_en`, `occurred_at`, `duration_minutes`, `affected_components` (M2M → `ServiceComponent`), `published` (bool, default `True`).
  - `created_by`/`updated_by` FK → `identity.User`. Every create/update calls `apps.core.services.audit()` (AGENTS red line #4 — audited mutations, even on a non-tenant model; `audit()` accepts an explicit actor and doesn't require tenant context).

- **`StatusSubscriber`**
  - `email` (unique), `subscribed_at`, `unsubscribe_token` (UUID, unique).

## 4. RBAC — new PLATFORM scope

**Problem:** `RoleAssignment` is a `TenantModel` — every row, including `FOUNDATION`-scope ones, carries its own tenant `foundation_id` and is read through the fail-closed `TenantManager` (`apps/core/managers.py:35-41`), which returns `.none()` with no thread-local `foundation_id`. A true platform-wide role has no `foundation_id` and can't live in that table without special-casing `TenantManager` itself — too invasive for this feature.

**Resolution — additive, no tenancy-layer changes:**
- New `apps/identity/models.py`: `PlatformRoleAssignment(models.Model)` — plain, non-tenant. Fields: `user` FK → `User`, `role` (choice, starting with just `platform_operator`), `created_at`. `unique_together(user, role)`.
- New `apps/identity/rbac.py`: `has_platform_permission(user, permission_key)` — checks `user.is_authenticated`/`is_active`/not locked, then whether the user holds a `PlatformRoleAssignment` whose role grants `permission_key` (a small static dict, e.g. `{'platform_operator': {'status.write'}}`, same shape as the existing foundation/school permission-matrix pattern already in `rbac.py`).
- `apps/identity/permissions.py`, `HasRequiredPermission.has_permission()`: insert a platform check right after the existing `is_authenticated` gate (line 22) and before the `foundation_id` resolution (line 39) — `has_platform_permission` short-circuits `True`/`False` without needing tenant context at all; only falls through to the existing `RoleAssignment` path if the view's `required_permission` isn't a platform-only key.
- New permission key: `status.write`.
- `PlatformRoleAssignmentAdmin` registered in `apps/identity/admin.py` so the first platform operator can be granted without a data migration hack.
- `TenantManager`, `TenancyMiddleware`, and `RoleAssignment` are not modified by this feature.

## 5. Cron (`apps/status/management/commands/check_service_health.py`)

- Subclasses `CronHostCommand` (`apps/core/management/base.py`), wrapped in `advisory_lock('check_service_health', timeout=0)`, logged via `JobRun` — same shape as `apps/hardware/management/commands/check_device_health.py`.
- `deploy/crontab` entry: `*/5 * * * *` (5-minute cadence — matches the existing `check_device_health` entry, appropriate for a lightweight self-check).
- Step 1: probe DB connectivity (`django.db.connection.ensure_connection()`/a trivial `SELECT 1`) and measure round-trip latency in ms.
- Step 2: write one `ComponentHeartbeat` per `ServiceComponent` with that `is_up`/`latency_ms`, unless the component has a `manual_status` override — that component still gets a heartbeat row for history continuity, but `is_up` reflects the manual override instead of the DB probe.
- Step 3: upsert today's `DailyComponentStatus` per component from today's heartbeats so far (majority-up-down-degraded rule: any `DOWN` heartbeat today → `DOWN`; else any degraded/`manual_status=DEGRADED` → `DEGRADED`; else `OPERATIONAL`). Idempotent — safe to run every 5 minutes.

## 6. Public page (`apps/status/views.py`, `apps/status/urls.py`)

- `GET /status/` — `TemplateView`, no auth, no `required_permission`, no tenant context needed (confirmed `TenancyMiddleware` sets `foundation_id = None` for unauthenticated requests and proceeds rather than blocking — same as `PartnerApiView`).
  - Banner: green/"all systems operational" unless any component's latest `DailyComponentStatus` (today) is `DEGRADED`/`DOWN`, in which case the banner reflects the worst current status.
  - Metric 1: uptime % = (days with `OPERATIONAL` status across all components, last 90 days) / (component-days in window), rounded 2dp, id-ID comma-decimal formatting (`99,94%`).
  - Metric 2: average `latency_ms` across `ComponentHeartbeat` rows in the last 90 days.
  - Metric 3: count of `StatusIncident` where `published=True` and no resolution marker in the current day (mockup shows "0 open incidents" — since this slice has no incident-open/closed state beyond the historical list, this metric is simply hardcoded to the count of incidents whose `occurred_at` is today and un-resolved; documented as a coarse approximation, refined only if a real need surfaces).
  - Component grid: `ServiceComponent`s ordered by `display_order`, each with last-30-days `DailyComponentStatus` as color bars (green/amber/red) and current-day status dot/label.
  - Incident list: `StatusIncident.objects.filter(published=True).order_by('-occurred_at')`, severity tag, title, date, duration, body, affected component chips.
  - Subscribe form posts to `/status/subscribe` (see below).
- `POST /status/subscribe` — public, CSRF-protected. Creates `StatusSubscriber` if the email doesn't already exist (idempotent — no error on duplicate), generates `unsubscribe_token`. Renders a simple confirmation (no email verification loop — out of scope per §1).
- i18n: a `STATUS_STRINGS` dict (id-ID + en) inside `apps/status`, reusing verbatim the Indonesian/English copy already written for the `st_*` keys in the design mockup's translation object (extracted in this session — eyebrow/heading/banner/metric-labels/component names+notes/incident severities/subscribe copy), consistent with the mockup's exact wording rather than re-translating.

## 7. Internal management view

- `/status/manage/` — gated by `HasRequiredPermission` + `status.write` (platform permission, §4). Lists `ServiceComponent`s with an edit form for `manual_status`; lists/creates/edits `StatusIncident` (with `published` toggle).
- All 5 models also registered in Django admin (`apps/status/admin.py`) as a fallback/audit surface, consistent with every other EduCore app.

## 8. Testing

- Model tests: `DailyComponentStatus` upsert rule, `ServiceComponent.manual_status` override precedence over heartbeat.
- Cron tests: heartbeat + rollup correctness, advisory-lock double-run is a no-op, `JobRun` success/failure recorded.
- Public page tests: banner color logic (all-ok vs one degraded/down), metric computation (uptime %, avg latency, id-ID formatting), bar-strip rendering, incident list ordering/filtering by `published`, i18n string switch.
- Platform-permission tests: a user without `PlatformRoleAssignment` gets 403 on `/status/manage/`; a `platform_operator` succeeds; confirm `PlatformRoleAssignment` never appears in any `TenantManager`-scoped queryset (regression guard for the tenancy-safety concern in §4).
- Subscribe endpoint tests: new email creates a row, duplicate email is idempotent (no duplicate row, no error), `unsubscribe_token` is generated and unique.

Target ≥80% coverage on `apps/status` business logic per USER.md §2.

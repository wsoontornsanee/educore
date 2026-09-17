# Parent App: Analytics Event Instrumentation — Design

**Source:** Notion [Open Item] Parent App: Analytics Event Instrumentation
(deferred from Step 10.1). `spec/08-parent-app.md` §5, `spec/15-reporting-and-analytics.md` §5 (RPT-015).

## Scope

Minimal event-sink for the 12 spec-named product analytics events, plus mobile
instrumentation at the call sites that exist today. No analytics pipeline
exists anywhere in this repo (teacher or parent app) — this is new
infrastructure, but built entirely from established patterns already in the
codebase (see below), not a new architectural style.

**In scope:** capture + durable storage of events. **Out of scope** (separate,
larger feature): the RPT-012..016 rollup dashboards / north-star WAU metric —
spec/15 §6 defines no ingestion API for these events at all; only the event
names and required fields are specified. The stored schema must be queryable
enough to later feed those rollups, but building them is not this task.

## Backend

**Model** — `apps/core/models.py`, new `AnalyticsEvent(TenantModel)`:
- `event_name` — `CharField(max_length=32, choices=EVENT_NAME_CHOICES)`, the
  12 names from spec/08 §5.
- `school_id` — `PositiveIntegerField(null=True, blank=True)` (many events,
  e.g. `app_open`, aren't school-scoped).
- `role` — `CharField(max_length=32)`, resolved server-side from the
  authenticated user's roles, never trusted from the client.
- `occurred_at` — `DateTimeField()`, client-reported event time (distinct
  from `TenantModel.created_at`, the server ingest time — mobile events are
  batched/offline-queued so these can differ).

Deliberately does **not** reuse `AuditEvent` (`apps/core/models.py:51-74`):
that model carries `actor_id`, `ip_address`, `diff` — fields that don't fit
"never PII" (RPT-015) and would sit unused. `AnalyticsEvent` inherits
`TenantModel` for the mandatory 3-layer tenancy (foundation_id + manager +
indexes), same as every other tenant table in the repo.

**Endpoint** — `POST /analytics/events/` in `apps/core/views.py` (new view),
wired in `apps/core/urls.py`:
- Body: `{"events": [{"event_name": "...", "school_id": 1|null, "occurred_at": "iso8601"}, ...]}`
  (batch, mirrors the mobile offline-queue batching pattern).
- `foundation_id` and `role` are resolved server-side from
  `request.user`/tenancy context — never accepted from the request body,
  closing the same spoofing gap `VerifyOtpView` had to guard against.
- Uses `apps.core.idempotency.IdempotentRequestMixin` (`apps/core/idempotency.py`)
  with the client-supplied `Idempotency-Key` header — satisfies
  `memory/00_CORE.md` rule 4.6 ("every ingest endpoint is idempotent").
- Permission: new key `analytics.event.write` added to every role's set in
  `apps/identity/rbac.py` `ROLE_PERMISSIONS` (all staff roles + `parent`).
  Consistent with the codebase's existing convention of explicit permission
  keys per action rather than a bare `IsAuthenticated` carve-out.
- Response: `201 {"accepted": <count>}`. Malformed individual events in a
  batch are skipped and counted, not fatal to the whole batch (analytics
  must never block or crash on a bad row).

## Mobile

**`mobile/src/services/analyticsQueue.ts`** (new) — mirrors the
`offlineQueue.ts` skeleton (SQLite table `analytics_event_queue`, in-memory
`Map` fallback for Jest/Node):
- `enqueueEvent(eventName: string, schoolId: number | null): void` — writes a
  PENDING row with `occurred_at = new Date().toISOString()`.
- `syncPendingEvents(): Promise<{total, succeeded, failed}>` — batches all
  PENDING/FAILED rows FIFO, POSTs via `apiClient.post('/analytics/events/', ...)`
  with an `Idempotency-Key`, marks SYNCED/FAILED all-or-nothing per batch
  (same shape as `offlineQueue.ts`'s `syncPendingEntries`).

**`mobile/src/services/analytics.ts`** (new) — thin wrapper:
- `track(eventName: AnalyticsEventName, schoolId?: number | null): void` —
  calls `enqueueEvent`, then fires `syncPendingEvents()` in the background
  (not awaited by the caller; errors swallowed). Tracking must never block or
  break the UI it's attached to.
- `AnalyticsEventName` union type = the 9 events wired in this task (see
  below).

**Call sites wired now** (event has an existing screen):
| Event | Site |
|---|---|
| `app_open` | `App.tsx` bootstrap, after successful `checkAuth()` |
| `child_switch` | `ParentShell.tsx`, child-switcher `onSelectChild` |
| `invoice_view` | `ParentInvoicesScreen.tsx` mount effect |
| `pay_start` | `PaymentScreen.tsx` mount effect |
| `pay_method_selected` | `PaymentScreen.tsx`, VA/QRIS method choice handler |
| `pay_intent_created` | `PaymentScreen.tsx`, after intent POST succeeds |
| `pay_completed` | `PaymentScreen.tsx`, poll loop on terminal `COMPLETED` status |
| `topup_completed` | `ParentWalletScreen.tsx`, poll loop on terminal top-up settlement |
| `notification_opened` | `App.tsx`, `subscribeToNotificationReceived` handler, `category` from `notification.data.type` |

**Not wired** (no screen exists yet — separate deferred Open Items):
`grades_view`, `report_card_view` (Academic Tab), `absence_submitted`
(Absence Request). `AnalyticsEventName` and the backend's `EVENT_NAME_CHOICES`
both list all 12 names so no migration is needed when those screens land —
only a new `track(...)` call site is needed then.

`school_id` is passed as `null` everywhere in this task: none of today's
mobile types (`ChildSummary`, `LinkedStudentProfile`) carry a numeric
`school_id`, only `school_name`. Adding it is out of scope here — the column
is nullable specifically so this doesn't block ingestion, and it can be
populated once a call site has that value.

## Testing

- Backend: `apps/core/tests/test_analytics_events.py` — valid batch persists
  correct rows scoped to the authenticated user's own foundation
  (cross-foundation isolation test, following the established pattern from
  every other tenant-scoped endpoint test in this repo); malformed event in
  a batch is skipped, not fatal; missing `Idempotency-Key` still succeeds
  (mixin no-ops without a key, per its documented contract); repeated same
  key does not double-insert.
- Mobile: `mobile/__tests__/analytics.test.ts` (`node --test`, matching the
  existing `offlineQueue`/`receipts` test style) — `enqueueEvent` writes a
  PENDING row; `syncPendingEvents` marks SYNCED on success and FAILED
  (retryable) on network error; `track()` never throws even when the queue
  or network fails.

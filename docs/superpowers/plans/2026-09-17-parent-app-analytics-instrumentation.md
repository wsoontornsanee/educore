# Parent App: Analytics Event Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal, PII-free event-sink (backend model + ingestion endpoint) for the 12 spec-named parent-app product analytics events, and wire the 9 of them that have an existing mobile screen today.

**Architecture:** A new `AnalyticsEvent` tenant model in `apps.core` (mirroring `TenantModel`, distinct from the PII-bearing `AuditEvent`), fed by a single idempotent batch-ingestion endpoint (`POST /api/v1/analytics/events/`) reusing the existing `IdempotentViewMixin`. Mobile side mirrors the existing `offlineQueue.ts` SQLite-queue-plus-batch-sync skeleton in a new `analyticsQueue.ts`, wrapped by a thin `analytics.ts` `track()` helper that fires and forgets.

**Tech Stack:** Django REST Framework, MySQL (via `TenantModel`), React Native + Expo SQLite, `node --test`.

## Global Constraints

- Every tenant table MUST inherit `apps.core.models.TenantModel` (3-layer tenancy: model + `TenantManager` + cross-tenant test) — `memory/00_CORE.md` §2.
- No PII in analytics events (`memory/00_CORE.md` #8, RPT-015 `spec/15-reporting-and-analytics.md:77`). `AnalyticsEvent` carries only `foundation_id`, `school_id`, `role`, `event_name`, `occurred_at` — no `actor_id`, no `ip_address`, no free-form `diff`/payload.
- Every mutating/ingest endpoint MUST be idempotent (`memory/00_CORE.md` #6, rule 4.6) — use `apps.core.idempotency.IdempotentViewMixin` with the client's `Idempotency-Key` header.
- Fail closed on permissions (`memory/00_CORE.md` #9, IAM-010) — the endpoint MUST declare `required_permission` via `HasRequiredPermission`, never a bare `IsAuthenticated`.
- `foundation_id` and `role` are resolved server-side from the authenticated request context, never accepted from the client body (same spoofing guard as `VerifyOtpView` in `apps/identity/views.py`).
- `id-ID` first (`memory/00_CORE.md` — any user-facing string, e.g. error messages, is Indonesian first).
- The 12 event names, verbatim, from `spec/08-parent-app.md` §5: `app_open, child_switch, invoice_view, pay_start, pay_method_selected, pay_intent_created, pay_completed, topup_completed, absence_submitted, grades_view, report_card_view, notification_opened`. Only the first 9 get a mobile call site in this plan (see Task 4) — `grades_view`, `report_card_view`, `absence_submitted` have no screen yet (separate deferred Open Items) but are still valid `event_name` choices so no migration is needed when those screens land.

---

### Task 1: Backend — `AnalyticsEvent` model and migration

**Files:**
- Modify: `apps/core/models.py` (append new model after `ExportJob`, end of file)
- Create: `apps/core/migrations/0006_analyticsevent.py` (the latest existing migration in `apps/core/migrations/` is `0005_auditevent_audit_event_foundat_751cce_idx.py` — `makemigrations` in Step 3 will generate this automatically; just confirm the number matches before committing)
- Test: `apps/core/tests/test_analytics_event_model.py`

**Interfaces:**
- Produces: `apps.core.models.AnalyticsEvent` with fields `event_name: str`, `school_id: Optional[int]`, `role: str`, `occurred_at: datetime`, plus everything `TenantModel` provides (`foundation_id`, `created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at`). Produces `apps.core.models.ANALYTICS_EVENT_NAMES: tuple[str, ...]` — the 12 event name strings, for Task 2 to reuse as the request-body validation set.

- [ ] **Step 1: Write the failing test**

Create `apps/core/tests/test_analytics_event_model.py`:

```python
"""Tests for AnalyticsEvent model (spec/08 §5, spec/15 RPT-015)."""
from django.test import TestCase
from django.utils import timezone
from apps.core.models import AnalyticsEvent, ANALYTICS_EVENT_NAMES
from educore.middleware.tenancy import tenant_context


class AnalyticsEventModelTests(TestCase):
    def test_create_and_scope_by_foundation(self):
        with tenant_context(1):
            AnalyticsEvent.objects.create(
                foundation_id=1,
                event_name='app_open',
                school_id=None,
                role='parent',
                occurred_at=timezone.now(),
            )
        with tenant_context(2):
            AnalyticsEvent.objects.create(
                foundation_id=2,
                event_name='app_open',
                school_id=None,
                role='parent',
                occurred_at=timezone.now(),
            )

        with tenant_context(1):
            self.assertEqual(AnalyticsEvent.objects.count(), 1)
        with tenant_context(2):
            self.assertEqual(AnalyticsEvent.objects.count(), 1)

    def test_event_name_choices_cover_full_spec_list(self):
        self.assertEqual(
            set(ANALYTICS_EVENT_NAMES),
            {
                'app_open', 'child_switch', 'invoice_view', 'pay_start',
                'pay_method_selected', 'pay_intent_created', 'pay_completed',
                'topup_completed', 'absence_submitted', 'grades_view',
                'report_card_view', 'notification_opened',
            },
        )

    def test_model_has_no_pii_fields(self):
        field_names = {f.name for f in AnalyticsEvent._meta.get_fields()}
        for forbidden in ('actor_id', 'ip_address', 'diff', 'full_name', 'phone_e164'):
            self.assertNotIn(forbidden, field_names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.core.tests.test_analytics_event_model -v 2`
Expected: FAIL with `ImportError: cannot import name 'AnalyticsEvent'`

- [ ] **Step 3: Write minimal implementation**

Append to `apps/core/models.py`, after the `ExportJob` class (end of file):

```python
ANALYTICS_EVENT_NAMES = (
    'app_open', 'child_switch', 'invoice_view', 'pay_start',
    'pay_method_selected', 'pay_intent_created', 'pay_completed',
    'topup_completed', 'absence_submitted', 'grades_view',
    'report_card_view', 'notification_opened',
)


class AnalyticsEvent(TenantModel):
    """Product analytics event (spec/08 §5, spec/15 RPT-015).

    Deliberately distinct from AuditEvent: no actor_id, ip_address, or diff —
    RPT-015 requires these events carry never PII, only foundation_id,
    school_id, and role.
    """
    EVENT_NAME_CHOICES = [(name, name) for name in ANALYTICS_EVENT_NAMES]

    event_name = models.CharField(max_length=32, choices=EVENT_NAME_CHOICES, db_index=True)
    school_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    role = models.CharField(max_length=32)
    occurred_at = models.DateTimeField(help_text="Client-reported event time; may differ from created_at for offline-queued events")

    class Meta:
        db_table = 'analytics_events'
        indexes = [
            models.Index(fields=['foundation_id', 'event_name', 'occurred_at']),
            models.Index(fields=['foundation_id', 'occurred_at']),
        ]

    def __str__(self):
        return f"{self.event_name} @ {self.occurred_at} (foundation={self.foundation_id})"
```

Then generate the migration:

```bash
python manage.py makemigrations core
```

Verify the generated filename and open it to confirm it only adds the `AnalyticsEvent` table (no unrelated changes) — if unexpected other model changes appear, stop and report them rather than committing an unrelated migration.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.core.tests.test_analytics_event_model -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/core/models.py apps/core/migrations/ apps/core/tests/test_analytics_event_model.py
git commit -m "feat(core): add AnalyticsEvent model for parent-app instrumentation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Backend — ingestion endpoint, permission key, URL wiring

**Files:**
- Modify: `apps/identity/rbac.py` — add `'analytics.event.write'` to every role's set in `ROLE_PERMISSIONS` (all 7 roles: `ROLE_FOUNDATION_ADMIN`, `ROLE_SCHOOL_ADMIN`, `ROLE_FINANCE_OFFICER`, `ROLE_TEACHER`, `ROLE_COUNSELLOR`, `ROLE_CANTEEN_OPERATOR`, `ROLE_PARENT`)
- Create: `apps/core/analytics_serializers.py`
- Modify: `apps/core/views.py` (append `AnalyticsEventIngestView`)
- Create: `apps/core/analytics_urls.py`
- Modify: `educore/urls.py` (add one `include` line)
- Test: `apps/identity/tests/test_analytics_ingest_endpoint.py` (uses real Bearer-JWT auth, matching `apps/identity/tests/test_guardian_children_endpoint.py`'s established pattern — this endpoint is reachable by every role, and cross-role/cross-foundation correctness matters more than any one app's internals)

**Interfaces:**
- Consumes: `apps.core.models.AnalyticsEvent`, `ANALYTICS_EVENT_NAMES` (Task 1). `apps.core.idempotency.IdempotentViewMixin` (existing). `apps.identity.permissions.HasRequiredPermission` (existing). `educore.middleware.tenancy.get_current_foundation_id` (existing, used the same way as `apps/core/views.py`'s `InitiateUploadView`).
- Produces: `POST /api/v1/analytics/events/` — request body `{"events": [{"event_name": str, "school_id": int|null, "occurred_at": iso8601 str}, ...]}`, response `201 {"accepted": int}`.

- [ ] **Step 1: Write the failing test**

Create `apps/identity/tests/test_analytics_ingest_endpoint.py`:

```python
"""Tests for POST /analytics/events/ (spec/08 §5, spec/15 RPT-015)."""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from apps.core.models import AnalyticsEvent
from apps.identity.models import Foundation, RoleAssignment, User


class AnalyticsIngestEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.foundation = Foundation.objects.create(legal_name="Yayasan Nusantara", brand_name="Nusantara")
        self.parent_user = User.all_tenants.create_user(
            phone_e164="+6281200000020", full_name="Ibu Sari", foundation_id=self.foundation.id,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.parent_user,
            role=RoleAssignment.ROLE_PARENT, scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

    def _auth(self, user):
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_valid_batch_persists_events_scoped_to_own_foundation(self):
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"},
                    {"event_name": "invoice_view", "school_id": 5, "occurred_at": "2026-09-17T08:01:00Z"},
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-1',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['accepted'], 2)

        events = AnalyticsEvent.all_tenants.filter(foundation_id=self.foundation.id)
        self.assertEqual(events.count(), 2)
        by_name = {e.event_name: e for e in events}
        self.assertEqual(by_name['app_open'].role, 'parent')
        self.assertIsNone(by_name['app_open'].school_id)
        self.assertEqual(by_name['invoice_view'].school_id, 5)

    def test_role_and_foundation_are_never_taken_from_request_body(self):
        """A client cannot spoof another foundation or role by including it in the body."""
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {
                        "event_name": "app_open", "school_id": None,
                        "occurred_at": "2026-09-17T08:00:00Z",
                        "foundation_id": 999999, "role": "foundation_admin",
                    },
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-2',
        )
        self.assertEqual(response.status_code, 201)
        event = AnalyticsEvent.all_tenants.get(foundation_id=self.foundation.id)
        self.assertEqual(event.role, 'parent')
        self.assertNotEqual(event.foundation_id, 999999)

    def test_unknown_event_name_in_batch_is_skipped_not_fatal(self):
        self._auth(self.parent_user)
        response = self.client.post(
            '/api/v1/analytics/events/',
            {
                "events": [
                    {"event_name": "not_a_real_event", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"},
                    {"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:01:00Z"},
                ]
            },
            format='json',
            HTTP_IDEMPOTENCY_KEY='test-key-3',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['accepted'], 1)

    def test_repeated_idempotency_key_does_not_double_insert(self):
        self._auth(self.parent_user)
        body = {"events": [{"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"}]}
        r1 = self.client.post('/api/v1/analytics/events/', body, format='json', HTTP_IDEMPOTENCY_KEY='dup-key')
        r2 = self.client.post('/api/v1/analytics/events/', body, format='json', HTTP_IDEMPOTENCY_KEY='dup-key')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(AnalyticsEvent.all_tenants.filter(foundation_id=self.foundation.id).count(), 1)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(
            '/api/v1/analytics/events/',
            {"events": [{"event_name": "app_open", "school_id": None, "occurred_at": "2026-09-17T08:00:00Z"}]},
            format='json',
        )
        self.assertEqual(response.status_code, 401)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.identity.tests.test_analytics_ingest_endpoint -v 2`
Expected: FAIL with 404 (URL not wired) or connection error

- [ ] **Step 3: Write minimal implementation**

In `apps/identity/rbac.py`, add `'analytics.event.write'` as a new line inside each of the 7 sets in `ROLE_PERMISSIONS` (e.g. for `ROLE_TEACHER`, change:
```python
    ROLE_TEACHER: {
        'student_records.read',
        'grades.read', 'grades.write',
        'attendance.read', 'attendance.write',
        'behaviour.read', 'behaviour.write',
    },
```
to:
```python
    ROLE_TEACHER: {
        'student_records.read',
        'grades.read', 'grades.write',
        'attendance.read', 'attendance.write',
        'behaviour.read', 'behaviour.write',
        'analytics.event.write',
    },
```
) — repeat for all 7 role keys.

Create `apps/core/analytics_serializers.py`:

```python
"""Serializers for POST /analytics/events/ (spec/08 §5)."""
from rest_framework import serializers
from apps.core.models import ANALYTICS_EVENT_NAMES


class AnalyticsEventItemSerializer(serializers.Serializer):
    event_name = serializers.ChoiceField(choices=ANALYTICS_EVENT_NAMES)
    school_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    occurred_at = serializers.DateTimeField()


class AnalyticsEventBatchSerializer(serializers.Serializer):
    events = serializers.ListField(child=serializers.DictField(), allow_empty=False)
```

Append to `apps/core/views.py`:

```python
from apps.core.analytics_serializers import AnalyticsEventBatchSerializer, AnalyticsEventItemSerializer
from apps.core.idempotency import IdempotentViewMixin
from apps.core.models import AnalyticsEvent
from apps.identity.models import RoleAssignment


class AnalyticsEventIngestView(IdempotentViewMixin, APIView):
    """POST /api/v1/analytics/events/ — batch ingest of parent/staff app product
    analytics events (spec/08 §5, spec/15 RPT-015). foundation_id and role are
    always resolved server-side; never trusted from the request body.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'analytics.event.write'

    def post(self, request):
        batch = AnalyticsEventBatchSerializer(data=request.data)
        batch.is_valid(raise_exception=True)

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        role_assignment = RoleAssignment.all_tenants.filter(
            foundation_id=foundation_id, user=request.user, deleted_at__isnull=True,
        ).values_list('role', flat=True).first()
        role = role_assignment or ''

        accepted = 0
        for raw_event in batch.validated_data['events']:
            item = AnalyticsEventItemSerializer(data=raw_event)
            if not item.is_valid():
                continue
            AnalyticsEvent.objects.create(
                foundation_id=foundation_id,
                event_name=item.validated_data['event_name'],
                school_id=item.validated_data.get('school_id'),
                role=role,
                occurred_at=item.validated_data['occurred_at'],
            )
            accepted += 1

        return Response({'accepted': accepted}, status=201)
```

Note: `apps/core/views.py` already imports `APIView`, `Response`, `HasRequiredPermission`, and `get_current_foundation_id` at the top of the file (confirmed from `InitiateUploadView` above it) — only add the four new imports shown, not duplicates of those.

Create `apps/core/analytics_urls.py`:

```python
from django.urls import path
from apps.core.views import AnalyticsEventIngestView

urlpatterns = [
    path('analytics/events/', AnalyticsEventIngestView.as_view(), name='analytics-event-ingest'),
]
```

In `educore/urls.py`, add one line after the `apps.core.urls` include:

```python
    path('api/v1/files/', include('apps.core.urls')),
    path('api/v1/', include('apps.core.analytics_urls')),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.identity.tests.test_analytics_ingest_endpoint -v 2`
Expected: PASS (5 tests)

Also run the full existing RBAC/permission test suites to confirm the new permission key didn't break any fail-closed assumption:

Run: `python manage.py test apps.identity -v 2`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add apps/identity/rbac.py apps/core/analytics_serializers.py apps/core/views.py apps/core/analytics_urls.py educore/urls.py apps/identity/tests/test_analytics_ingest_endpoint.py
git commit -m "feat(core): add idempotent analytics event ingestion endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Mobile — `analyticsQueue.ts` and `analytics.ts`

**Files:**
- Create: `mobile/src/services/analyticsQueue.ts`
- Create: `mobile/src/services/analytics.ts`
- Test: `mobile/__tests__/analytics.test.ts`

**Interfaces:**
- Consumes: `mobile/src/services/api.ts`'s `apiClient` (existing), `mobile/src/services/offlineQueue.ts`'s `generateUUID` pattern (duplicated locally, not imported, to keep `analyticsQueue.ts` self-contained the way `posOfflineQueue.ts` does — confirm this by checking whether `posOfflineQueue.ts` imports `generateUUID` from `offlineQueue.ts` or redefines it; if it imports, import here too instead of duplicating).
- Produces: `analyticsQueue.ts` exports `initAnalyticsQueueDb(): Promise<void>`, `enqueueEvent(eventName: string, schoolId: number | null): Promise<void>`, `getPendingEventCount(): Promise<number>`, `syncPendingEvents(): Promise<{total: number; succeeded: number; failed: number}>`, `clearAllAnalyticsForTesting(): Promise<void>`. `analytics.ts` exports `type AnalyticsEventName = 'app_open' | 'child_switch' | 'invoice_view' | 'pay_start' | 'pay_method_selected' | 'pay_intent_created' | 'pay_completed' | 'topup_completed' | 'notification_opened'` and `track(eventName: AnalyticsEventName, schoolId?: number | null): void`.

- [ ] **Step 1: Write the failing test**

Create `mobile/__tests__/analytics.test.ts`:

```typescript
/**
 * Mobile Analytics Event Instrumentation Unit Tests (spec/08 §5, spec/15 RPT-015).
 */
import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  clearAllAnalyticsForTesting,
  enqueueEvent,
  getPendingEventCount,
  syncPendingEvents,
} from '../src/services/analyticsQueue.ts';
import { track } from '../src/services/analytics.ts';
import { apiClient } from '../src/services/api.ts';

describe('Analytics Event Queue', () => {
  beforeEach(async () => {
    await clearAllAnalyticsForTesting();
  });

  it('enqueues an event as PENDING', async () => {
    await enqueueEvent('app_open', null);
    const count = await getPendingEventCount();
    assert.strictEqual(count, 1);
  });

  it('syncs pending events in one batch to /analytics/events/', async () => {
    await enqueueEvent('app_open', null);
    await enqueueEvent('invoice_view', 5);

    let sentPayload: any = null;
    const originalPost = apiClient.post;
    (apiClient as any).post = async (url: string, data: any) => {
      if (url === '/analytics/events/') {
        sentPayload = data;
        return { data: { accepted: 2 }, status: 201, headers: {} };
      }
      return originalPost(url, data);
    };

    try {
      const result = await syncPendingEvents();
      assert.strictEqual(result.total, 2);
      assert.strictEqual(result.succeeded, 2);
      assert.strictEqual(result.failed, 0);
      assert.strictEqual(sentPayload.events.length, 2);
      assert.strictEqual(sentPayload.events[0].event_name, 'app_open');
      assert.strictEqual(sentPayload.events[1].school_id, 5);

      const remaining = await getPendingEventCount();
      assert.strictEqual(remaining, 0);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('marks events FAILED (retryable) on network error, does not throw', async () => {
    await enqueueEvent('app_open', null);

    const originalPost = apiClient.post;
    (apiClient as any).post = async () => {
      throw new Error('Network Error');
    };

    try {
      const result = await syncPendingEvents();
      assert.strictEqual(result.failed, 1);
      assert.strictEqual(result.succeeded, 0);
      // Still pending (FAILED counts as retryable pending), not lost:
      const remaining = await getPendingEventCount();
      assert.strictEqual(remaining, 1);
    } finally {
      (apiClient as any).post = originalPost;
    }
  });

  it('track() never throws even when the queue/network fails', () => {
    assert.doesNotThrow(() => track('app_open'));
    assert.doesNotThrow(() => track('invoice_view', 5));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && node --test __tests__/analytics.test.ts`
Expected: FAIL with module not found for `../src/services/analyticsQueue.ts`

- [ ] **Step 3: Write minimal implementation**

Create `mobile/src/services/analyticsQueue.ts` (mirrors `offlineQueue.ts`'s skeleton exactly — SQLite table with in-memory `Map` fallback, FIFO batch sync):

```typescript
/**
 * SQLite-backed Offline Analytics Event Queue (spec/08 §5, spec/15 RPT-015).
 *
 * Enqueues product analytics events and batch-syncs them FIFO to
 * /analytics/events/. Never blocks or throws on the caller — analytics must
 * never break the screen it instruments.
 */
import { apiClient } from './api.ts';

export interface AnalyticsQueueItem {
  id: string;
  event_name: string;
  school_id: number | null;
  occurred_at: string;
  status: 'PENDING' | 'SYNCING' | 'SYNCED' | 'FAILED';
  attempts: number;
  created_at: string;
  last_error: string | null;
}

const memoryQueue: Map<string, AnalyticsQueueItem> = new Map();

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

let sqliteDb: any = null;
try {
  const SQLite = require('expo-sqlite');
  if (SQLite && typeof SQLite.openDatabaseSync === 'function') {
    sqliteDb = SQLite.openDatabaseSync('educore_offline.db');
  } else if (SQLite && typeof SQLite.openDatabase === 'function') {
    sqliteDb = SQLite.openDatabase('educore_offline.db');
  }
} catch {
  // Fallback to memoryQueue
}

export async function initAnalyticsQueueDb(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.execSync === 'function') {
    try {
      sqliteDb.execSync(`
        CREATE TABLE IF NOT EXISTS analytics_event_queue (
          id TEXT PRIMARY KEY,
          event_name TEXT,
          school_id INTEGER,
          occurred_at TEXT,
          status TEXT,
          attempts INTEGER,
          created_at TEXT,
          last_error TEXT
        );
      `);
      return;
    } catch {
      // Fallback
    }
  }
}

export async function enqueueEvent(eventName: string, schoolId: number | null): Promise<void> {
  const id = generateUUID();
  const now = new Date().toISOString();
  const item: AnalyticsQueueItem = {
    id,
    event_name: eventName,
    school_id: schoolId,
    occurred_at: now,
    status: 'PENDING',
    attempts: 0,
    created_at: now,
    last_error: null,
  };

  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `INSERT INTO analytics_event_queue (id, event_name, school_id, occurred_at, status, attempts, created_at, last_error)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [item.id, item.event_name, item.school_id, item.occurred_at, item.status, item.attempts, item.created_at, item.last_error]
      );
      return;
    } catch {
      // Fallback to memoryQueue
    }
  }

  memoryQueue.set(id, item);
}

async function getPendingEvents(): Promise<AnalyticsQueueItem[]> {
  if (sqliteDb && typeof sqliteDb.getAllSync === 'function') {
    try {
      const rows: any[] = sqliteDb.getAllSync(
        `SELECT * FROM analytics_event_queue WHERE status IN ('PENDING', 'FAILED') ORDER BY created_at ASC`
      );
      return rows.map((r) => ({ ...r }));
    } catch {
      // Fallback to memoryQueue
    }
  }

  return Array.from(memoryQueue.values())
    .filter((i) => i.status === 'PENDING' || i.status === 'FAILED')
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export async function getPendingEventCount(): Promise<number> {
  const items = await getPendingEvents();
  return items.length;
}

async function markEventStatus(id: string, status: AnalyticsQueueItem['status'], error: string | null = null): Promise<void> {
  const isAttempt = status === 'FAILED' || status === 'SYNCED';
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(
        `UPDATE analytics_event_queue SET status = ?, attempts = attempts + ?, last_error = ? WHERE id = ?`,
        [status, isAttempt ? 1 : 0, error, id]
      );
      return;
    } catch {
      // Fallback
    }
  }

  const item = memoryQueue.get(id);
  if (item) {
    item.status = status;
    if (isAttempt) item.attempts += 1;
    item.last_error = error;
  }
}

async function clearSyncedEvents(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM analytics_event_queue WHERE status = 'SYNCED'`);
      return;
    } catch {
      // Fallback
    }
  }

  for (const [key, item] of memoryQueue.entries()) {
    if (item.status === 'SYNCED') memoryQueue.delete(key);
  }
}

export async function clearAllAnalyticsForTesting(): Promise<void> {
  if (sqliteDb && typeof sqliteDb.runSync === 'function') {
    try {
      sqliteDb.runSync(`DELETE FROM analytics_event_queue`);
    } catch {
      // Fallback
    }
  }
  memoryQueue.clear();
}

export async function syncPendingEvents(): Promise<{ total: number; succeeded: number; failed: number }> {
  const pending = await getPendingEvents();
  if (pending.length === 0) {
    return { total: 0, succeeded: 0, failed: 0 };
  }

  for (const item of pending) {
    await markEventStatus(item.id, 'SYNCING');
  }

  const batchPayload = {
    events: pending.map((item) => ({
      event_name: item.event_name,
      school_id: item.school_id,
      occurred_at: item.occurred_at,
    })),
  };

  try {
    await apiClient.post('/analytics/events/', batchPayload, {
      headers: { 'Idempotency-Key': generateUUID() },
    });

    for (const item of pending) {
      await markEventStatus(item.id, 'SYNCED');
    }
    await clearSyncedEvents();

    return { total: pending.length, succeeded: pending.length, failed: 0 };
  } catch (err: any) {
    const errorMessage = err?.response?.data?.error || err.message || 'Sync failed';
    for (const item of pending) {
      await markEventStatus(item.id, 'FAILED', errorMessage);
    }
    return { total: pending.length, succeeded: 0, failed: pending.length };
  }
}
```

Create `mobile/src/services/analytics.ts`:

```typescript
/**
 * Product analytics tracking (spec/08 §5). Fire-and-forget: never throws,
 * never awaited by callers, never blocks the screen it instruments.
 */
import { enqueueEvent, syncPendingEvents } from './analyticsQueue.ts';

export type AnalyticsEventName =
  | 'app_open'
  | 'child_switch'
  | 'invoice_view'
  | 'pay_start'
  | 'pay_method_selected'
  | 'pay_intent_created'
  | 'pay_completed'
  | 'topup_completed'
  | 'notification_opened';

export function track(eventName: AnalyticsEventName, schoolId: number | null = null): void {
  enqueueEvent(eventName, schoolId)
    .then(() => syncPendingEvents())
    .catch(() => {
      // Analytics must never surface an error to the UI.
    });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && node --test __tests__/analytics.test.ts`
Expected: PASS (4 tests)

Also run the full mobile suite to confirm no regressions:

Run: `cd mobile && npm test`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add mobile/src/services/analyticsQueue.ts mobile/src/services/analytics.ts mobile/__tests__/analytics.test.ts
git commit -m "feat(mobile): add analytics event queue and track() helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Mobile — wire the 9 call sites

**Files:**
- Modify: `mobile/App.tsx` (bootstrap effect, notification handler)
- Modify: `mobile/src/screens/parent/ParentShell.tsx` (`handleSelectChild`)
- Modify: `mobile/src/screens/parent/ParentInvoicesScreen.tsx` (mount effect)
- Modify: `mobile/src/screens/parent/PaymentScreen.tsx` (mount effect, `handleChooseMethod`, poll tick)
- Modify: `mobile/src/screens/parent/ParentWalletScreen.tsx` (`startPolling`)

**Interfaces:**
- Consumes: `track` and `AnalyticsEventName` from `mobile/src/services/analytics.ts` (Task 3).

- [ ] **Step 1: Wire `app_open` and `notification_opened` in `App.tsx`**

In `mobile/App.tsx`, add the import near the other service imports (after line 9's `isParent` import):

```typescript
import { track } from './src/services/analytics.ts';
```

In the `bootstrap` function inside the first `useEffect` (around line 46-51), after the existing `if (authState.authenticated && authState.user) { ... }` block's `registerForPushNotificationsAsync().catch(() => {});` line, add:

```typescript
        registerForPushNotificationsAsync().catch(() => {});
        track('app_open');
```

In the `subscribeToNotificationReceived` callback (around line 58-63), after the existing `if (data?.type === 'SUBSTITUTE_ASSIGNED' && data?.slot) { setSubModalSlot(data.slot); }` line, add a call that fires regardless of notification type:

```typescript
    const sub = subscribeToNotificationReceived((notification) => {
      const data = notification?.request?.content?.data;
      if (data?.type === 'SUBSTITUTE_ASSIGNED' && data?.slot) {
        setSubModalSlot(data.slot);
      }
      track('notification_opened');
    });
```

- [ ] **Step 2: Wire `child_switch` in `ParentShell.tsx`**

In `mobile/src/screens/parent/ParentShell.tsx`, add the import after line 8 (`import { colors, ... } from '../../theme/tokens';`):

```typescript
import { track } from '../../services/analytics.ts';
```

In `handleSelectChild` (line 51-54), add the call:

```typescript
  const handleSelectChild = async (id: number) => {
    setSelectedId(id);
    await saveLastChildId(id);
    track('child_switch');
  };
```

- [ ] **Step 3: Wire `invoice_view` in `ParentInvoicesScreen.tsx`**

In `mobile/src/screens/parent/ParentInvoicesScreen.tsx`, add the import next to the existing service imports at the top of the file, then in the first `useEffect` (the invoice-loading one at line 69-93, keyed on `[child.student_id, reloadToken]`), add a `track('invoice_view')` call as the first line inside the effect's async function, before `setLoading`/`setLoadError` reset:

```typescript
  useEffect(() => {
    let cancelled = false;
    track('invoice_view');
    (async () => {
      // ...existing body unchanged...
```

(Read the exact surrounding lines with the Read tool before editing — the effect body's exact variable names must be preserved; only the one new line is inserted.)

- [ ] **Step 4: Wire `pay_start`, `pay_method_selected`, `pay_intent_created`, `pay_completed` in `PaymentScreen.tsx`**

In `mobile/src/screens/parent/PaymentScreen.tsx`, add the import after line 9 (`import { colors, ... } from '../../theme/tokens';`):

```typescript
import { track } from '../../services/analytics.ts';
```

Add a mount effect for `pay_start` right after the existing cleanup-only effect (lines 48-50):

```typescript
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  useEffect(() => {
    track('pay_start');
  }, []);
```

In `handleChooseMethod` (starts line 63), add `track('pay_method_selected')` as the first line of the function body, and `track('pay_intent_created')` right after `setIntent(created);`:

```typescript
  const handleChooseMethod = async (chosen: 'VA' | 'QRIS') => {
    track('pay_method_selected');
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setMethod(chosen);
    setIntent(null);
    setLoading(true);
    setErrorMsg(null);
    setTerminalMsg(null);
    try {
      const created = await createPaymentIntent(invoiceIds, chosen);
      setIntent(created);
      track('pay_intent_created');
      pollRef.current = setInterval(async () => {
```

In the poll tick, right before the existing `onDone();` call inside the `if (refreshed.status === SUCCESS_STATUS)` branch, add `track('pay_completed')`:

```typescript
          if (refreshed.status === SUCCESS_STATUS) {
            if (!pollRef.current) return;
            clearInterval(pollRef.current);
            pollRef.current = null;
            track('pay_completed');
            onDone();
          } else if (UNPAID_TERMINAL_MESSAGE[refreshed.status]) {
```

- [ ] **Step 5: Wire `topup_completed` in `ParentWalletScreen.tsx`**

In `mobile/src/screens/parent/ParentWalletScreen.tsx`, add the import next to the other service imports at the top of the file. In `startPolling` (around line 284-312), inside the `if (settledIntent.status === 'SETTLED')` branch, add the call before `await loadData(true)`:

```typescript
      if (settledIntent.status === 'SETTLED') {
        track('topup_completed');
        // Auto refresh wallet balance and transactions
        await loadData(true);
      }
```

- [ ] **Step 6: Verify no regressions**

Run: `cd mobile && npm test`
Expected: PASS (all existing tests unaffected — this task only adds `track(...)` calls, no behavior change to any existing return value or state transition)

Run a TypeScript check if the project has one configured (check `mobile/package.json` for a `typecheck`/`tsc` script; if present, run it):

Run: `cd mobile && npx tsc --noEmit` (or the project's equivalent script)
Expected: No new type errors

- [ ] **Step 7: Commit**

```bash
git add mobile/App.tsx mobile/src/screens/parent/ParentShell.tsx mobile/src/screens/parent/ParentInvoicesScreen.tsx mobile/src/screens/parent/PaymentScreen.tsx mobile/src/screens/parent/ParentWalletScreen.tsx
git commit -m "feat(mobile): instrument 9 parent-app analytics events

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

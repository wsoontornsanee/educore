# Parent App: Push-Driven Deep Link into Attendance Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ARRIVAL/DEPARTURE push notifications carry a structured data payload and make the parent mobile app deep-link into the correct child's attendance screen, scrolled to the correct date, whether tapped from a cold start or while the app is already open (PAR-004).

**Architecture:** Reuse the existing `dispatch_intent(...payload=...)` pipeline as the data-payload channel (no new model field) by making `MockPushProvider` actually record the `variables` it already receives, adding a `type` discriminator + identifying ids to the ARRIVAL/DEPARTURE/SUBSTITUTE_ASSIGNED payloads, wiring DEPARTURE dispatch (currently entirely missing), and adding cold-start + tap-while-open notification handling to the mobile app for the first time, driving a new `deepLinkChildId`/`highlightDate` prop chain through `ParentShell` → `ParentAttendanceScreen`.

**Tech Stack:** Django REST Framework, MySQL (via `TenantModel`), React Native (Expo) + `expo-notifications`, `node --test`.

## Global Constraints

- Every mutating action writes an audit event where one already exists in the surrounding code path — this plan does not add new audit-worthy actions (dispatching a notification is not itself an audited domain action in this codebase; no existing call site here does so either).
- `id-ID` first (`memory/00_CORE.md`) — any new user-facing string is Indonesian first. This plan introduces no new user-facing copy (no new template text, no new screen text beyond a highlight style).
- No PII in analytics/logs (`memory/00_CORE.md` #8) — the new payload fields (`student_id`, `date`, `substitution_id`) are internal numeric ids and ISO dates, not PII, matching the existing payload's own `student_name`/`guardian_name` precedent (already accepted in this codebase's notification payloads, which are internal `NotificationIntent.payload` JSON, never logged raw or exposed externally).
- Real push delivery (Expo/FCM HTTP send) is explicitly out of scope — every notification channel in this codebase is a `Mock*Provider` except `WhatsAppCloudApiProvider`; this plan does not add a real push-send integration, only makes the mock capture the data payload it already receives.
- Rebuilding the teacher-side "tap opens the substitution review modal" flow is explicitly out of scope (see design doc's Section 4) — logged as a separate Notion Open Item, not a task in this plan.
- No RN component test harness exists in this repo (`node --test` covers service/logic layer only) — new pure logic added for the mobile deep-link (child/date resolution) is extracted into small, directly unit-testable functions rather than tested through component rendering.

---

### Task 1: Backend — `MockPushProvider` captures its data payload

**Files:**
- Modify: `apps/notifications/providers.py:120-155` (`MockPushProvider.send`)
- Test: `apps/notifications/tests/test_push_provider_data_payload.py`

**Interfaces:**
- Produces: `MockPushProvider.dispatched_messages` entries now include a `'variables'` key (`Dict[str, Any]`), matching the shape `MockWhatsAppProvider.dispatched_messages` already uses for its own `'variables'` key.

- [ ] **Step 1: Write the failing test**

Create `apps/notifications/tests/test_push_provider_data_payload.py`:

```python
"""MockPushProvider must capture the variables dict it's given, so the
notification pipeline's payload is inspectable as the future push data
payload (spec/08 PAR-004)."""
from django.test import TestCase
from apps.notifications.providers import MockPushProvider


class MockPushProviderDataPayloadTests(TestCase):
    def test_send_records_variables_dict(self):
        provider = MockPushProvider()
        result = provider.send(
            recipient_target='device-token-abc',
            rendered_body='Anak Anda tiba di sekolah pukul 07:05.',
            rendered_subject='',
            template_key='attendance.arrival',
            variables={'type': 'ARRIVAL', 'student_id': 42, 'date': '2026-09-17'},
        )
        self.assertTrue(result.success)
        self.assertEqual(len(provider.dispatched_messages), 1)
        recorded = provider.dispatched_messages[0]
        self.assertEqual(
            recorded['variables'],
            {'type': 'ARRIVAL', 'student_id': 42, 'date': '2026-09-17'},
        )

    def test_send_records_empty_dict_when_variables_omitted(self):
        provider = MockPushProvider()
        provider.send(
            recipient_target='device-token-abc',
            rendered_body='Test',
        )
        self.assertEqual(provider.dispatched_messages[0]['variables'], {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.notifications.tests.test_push_provider_data_payload -v 2`
Expected: FAIL with `KeyError: 'variables'`

- [ ] **Step 3: Write minimal implementation**

In `apps/notifications/providers.py`, find `MockPushProvider.send` (currently):

```python
        msg_id = f"fcm.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'body': rendered_body,
            'subject': rendered_subject,
            'timestamp': timezone.now().isoformat(),
        })
        self.record_success()
```

Change the appended dict to add `'variables'`:

```python
        msg_id = f"fcm.{uuid.uuid4().hex}"
        self.dispatched_messages.append({
            'id': msg_id,
            'recipient': recipient_target,
            'body': rendered_body,
            'subject': rendered_subject,
            'variables': variables or {},
            'timestamp': timezone.now().isoformat(),
        })
        self.record_success()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.notifications.tests.test_push_provider_data_payload -v 2`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/notifications/providers.py apps/notifications/tests/test_push_provider_data_payload.py
git commit -m "feat(notifications): MockPushProvider records its data payload

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Backend — DEPARTURE dispatch + `student_id`/`type` in ARRIVAL/DEPARTURE payload

**Files:**
- Modify: `apps/notifications/services.py` (`handle_gate_scanned_event`, currently starting at line 504)
- Modify: `apps/attendance/services.py:495-501`
- Test: `apps/attendance/tests/test_departure_notification.py`
- Test: Modify `apps/notifications/tests/test_whatsapp_arrival.py` (one assertion added to the existing ARRIVAL test, see Step 3b)

**Interfaces:**
- Consumes: `apps.notifications.providers.MockPushProvider` (Task 1) for verifying the recorded data payload.
- Produces: `apps.notifications.services.handle_gate_scanned_event(event_payload, category=NotificationCategory.ARRIVAL)` — new optional `category` keyword, defaulting to today's ARRIVAL-only behavior so no other caller needs to change. `dispatch_intent`'s `payload` for both ARRIVAL and DEPARTURE now includes `'type'` (the category string) and `'student_id'` (int).

- [ ] **Step 1: Write the failing test**

Create `apps/attendance/tests/test_departure_notification.py`. This mirrors `apps/notifications/tests/test_whatsapp_arrival.py`'s fixture exactly (same models, same `issue_credential`/`ingest_gate_events` calls) but uses an OUT-direction device and asserts a DEPARTURE notification with the new payload shape:

```python
"""DEPARTURE gate scans must dispatch a notification per linked guardian,
mirroring the existing ARRIVAL path (spec/08 PAR-004, spec/05 §4 ATT-006)."""
import uuid
from django.test import TestCase
from django.utils import timezone
from apps.attendance.models import Credential, CredentialType
from apps.attendance.services import ingest_gate_events, issue_credential
from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, School, Student, User
from apps.notifications.models import IntentStatus, NotificationIntent
from apps.notifications.providers import MockPushProvider, register_provider
from educore.middleware.tenancy import tenant_context, set_current_foundation_id


class DepartureNotificationTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Islam Terpadu Al-Qalam",
            brand_name="Al-Qalam",
            npwp="01.234.567.8-111.001",
            address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SD IT Al-Qalam",
            npsn="20300002",
            level=School.LEVEL_SD,
        )
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010003",
            full_name="Umar bin Khattab",
            gender="M",
            dob="2015-06-11",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nisn="0012345679",
            nis="24002",
        )
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010004",
            full_name="Khalid bin Walid",
            gender="M",
            dob="1985-02-02",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281399887777",
            email="khalid@example.sch.id",
            full_name="Khalid bin Walid",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        self.link = GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            guardian=self.guardian,
            student=self.student,
            relation=GuardianLink.RELATION_FATHER,
            can_pickup=True,
            is_primary=True,
        )
        self.device = Device.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            device_code="GATE-OUT-01",
            name="Turnstile Gerbang Keluar",
            device_class=DeviceClass.GATE_READER,
            direction=DeviceDirection.OUT,
            status=DeviceStatus.ONLINE,
        )
        self.credential = issue_credential(
            foundation_id=str(self.foundation.id),
            student=self.student,
            type=CredentialType.RFID,
            uid="E280116060000205",
        )
        self.mock_push = MockPushProvider()
        register_provider('PUSH', self.mock_push)

    def test_gate_scan_out_produces_departure_notification(self):
        scan_time = timezone.now()
        events_payload = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000205',
            'occurred_at': scan_time.isoformat(),
            'method': 'RFID',
        }]

        with tenant_context(self.foundation.id):
            result = ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=events_payload,
            )
            self.assertEqual(result['accepted'], 1)

            intent = NotificationIntent.objects.filter(
                foundation_id=self.foundation.id,
                recipient_phone="+6281399887777",
                category="DEPARTURE",
            ).first()
            self.assertIsNotNone(intent)
            self.assertEqual(intent.status, IntentStatus.DISPATCHED)
            self.assertEqual(intent.payload['type'], 'DEPARTURE')
            self.assertEqual(intent.payload['student_id'], self.student.id)
            self.assertEqual(intent.payload['date'], scan_time.strftime('%Y-%m-%d'))

            self.assertEqual(len(self.mock_push.dispatched_messages), 1)
            self.assertEqual(
                self.mock_push.dispatched_messages[0]['variables']['type'], 'DEPARTURE',
            )
            self.assertEqual(
                self.mock_push.dispatched_messages[0]['variables']['student_id'], self.student.id,
            )

    def test_cross_foundation_isolation(self):
        """A DEPARTURE notification never leaks into another foundation's NotificationIntent set."""
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="09.876.543.2-000.000", address="Bandung",
        )
        scan_time = timezone.now()
        events_payload = [{
            'event_uuid': str(uuid.uuid4()),
            'device_id': str(self.device.id),
            'raw_uid': 'E280116060000205',
            'occurred_at': scan_time.isoformat(),
            'method': 'RFID',
        }]
        with tenant_context(self.foundation.id):
            ingest_gate_events(
                foundation_id=str(self.foundation.id),
                school_id=str(self.school.id),
                events_data=events_payload,
            )
        with tenant_context(other_foundation.id):
            leaked = NotificationIntent.objects.filter(category="DEPARTURE").exists()
            self.assertFalse(leaked)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.attendance.tests.test_departure_notification -v 2`
Expected: FAIL — no DEPARTURE `NotificationIntent` is ever created (direction `OUT` is never dispatched today)

- [ ] **Step 3: Write minimal implementation**

**3a.** In `apps/notifications/services.py`, find `handle_gate_scanned_event` (currently starting `def handle_gate_scanned_event(event_payload: Dict[str, Any]):` at line 504). Change its signature and body to accept and use a `category` parameter, and add `type`/`student_id` to the payload dict:

```python
def handle_gate_scanned_event(
    event_payload: Dict[str, Any],
    category: str = NotificationCategory.ARRIVAL,
):
    """
    Domain event listener for 'attendance.gate.scanned'.

    Generates parent arrival/departure notifications within 5 seconds
    (spec/05 §4 ATT-006, spec/13 §6, spec/08 PAR-004).
    """
    direction = event_payload.get('direction')
    student_id = event_payload.get('student_id')
    school_id = event_payload.get('school_id')
    occurred_at_str = event_payload.get('occurred_at')
    gate_event_id = event_payload.get('gate_event_id')

    expected_direction = 'IN' if category == NotificationCategory.ARRIVAL else 'OUT'
    if direction != expected_direction or not student_id:
        return

    try:
        student = Student.objects.select_related('school', 'person').get(id=student_id)
    except Student.DoesNotExist:
        logger.warning(f"Student #{student_id} not found for {category.lower()} notification.")
        return

    foundation_id = student.foundation_id

    if occurred_at_str:
        try:
            occurred_at = datetime.datetime.fromisoformat(occurred_at_str)
        except Exception:
            occurred_at = timezone.now()
    else:
        occurred_at = timezone.now()

    occurred_date_str = occurred_at.strftime('%Y-%m-%d')
    time_str = occurred_at.strftime('%H:%M')

    guardian_links = GuardianLink.objects.filter(
        foundation_id=foundation_id,
        student=student,
        deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    if not guardian_links.exists():
        logger.info(f"Student #{student.id} has no linked guardians. No {category.lower()} notice sent.")
        return

    school_name = student.school.name if student.school else 'Sekolah'
    student_name = student.person.full_name if student.person else 'Siswa'
    gate_name = event_payload.get('gate_name') or 'Gerbang Sekolah'
    template_key = 'attendance.arrival' if category == NotificationCategory.ARRIVAL else 'attendance.departure'

    for link in guardian_links:
        guardian = link.guardian
        user = guardian.user
        phone = (getattr(user, 'phone_e164', None) or getattr(user, 'phone', '')) if user else ''
        email = (getattr(user, 'email', '')) if user else ''
        guardian_name = guardian.person.full_name if guardian.person else 'Wali Murid'

        if not phone and not user:
            continue

        dedupe_key = f"{category.lower()}:{student.id}:{occurred_date_str}:{guardian.id}"

        payload = {
            'type': category,
            'student_id': student.id,
            'student_name': student_name,
            'guardian_name': guardian_name,
            'school_name': school_name,
            'gate_name': gate_name,
            'time': time_str,
            'date': occurred_date_str,
        }

        dispatch_intent(
            foundation_id=foundation_id,
            school_id=school_id,
            recipient_user=user,
            recipient_phone=phone,
            recipient_email=email,
            recipient_name=guardian_name,
            category=category,
            template_key=template_key,
            payload=payload,
            priority=NotificationPriority.HIGH,
            dedupe_key=dedupe_key,
            immediate=True,
        )
```

Note: `template_key='attendance.departure'` already exists — it's both
seeded in `apps/notifications/management/commands/seed_notification_templates.py`
and has a built-in fallback in `render_template_message`
(`apps/notifications/services.py:228-270`, the `elif template_key == 'attendance.departure':` branch). No template work is needed for this task.

**3b.** In `apps/attendance/services.py:495-501`, change:

```python
                # Dispatch parent arrival notification (ATT-006: < 5s)
                if direction == 'IN' and student:
                    try:
                        from apps.notifications.services import handle_gate_scanned_event
                        handle_gate_scanned_event(event_payload)
                    except Exception as exc:
                        logger.warning(f"Error handling gate scanned event notification: {exc}")
```

to:

```python
                # Dispatch parent arrival/departure notification (ATT-006: < 5s, PAR-004)
                if direction in ('IN', 'OUT') and student:
                    try:
                        from apps.notifications.models import NotificationCategory
                        from apps.notifications.services import handle_gate_scanned_event
                        category = NotificationCategory.ARRIVAL if direction == 'IN' else NotificationCategory.DEPARTURE
                        handle_gate_scanned_event(event_payload, category=category)
                    except Exception as exc:
                        logger.warning(f"Error handling gate scanned event notification: {exc}")
```

**3c.** Add one assertion to the existing ARRIVAL test in
`apps/notifications/tests/test_whatsapp_arrival.py`'s
`test_gate_scan_produces_arrival_notification_within_5s` (after the existing
`self.assertIsNotNone(intent)` / `self.assertEqual(intent.status, ...)`
lines), confirming the ARRIVAL payload also gained the new keys without
breaking anything already asserted:

```python
            self.assertEqual(intent.payload['type'], 'ARRIVAL')
            self.assertEqual(intent.payload['student_id'], self.student.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.attendance.tests.test_departure_notification apps.notifications.tests.test_whatsapp_arrival -v 2`
Expected: PASS (all tests, including the modified ARRIVAL test)

Also run the full notifications + attendance suites to confirm no regressions:

Run: `python manage.py test apps.notifications apps.attendance -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/notifications/services.py apps/attendance/services.py apps/attendance/tests/test_departure_notification.py apps/notifications/tests/test_whatsapp_arrival.py
git commit -m "feat(attendance): dispatch DEPARTURE notifications with deep-link payload

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Backend — `SUBSTITUTE_ASSIGNED` payload gains `type`/`substitution_id`

**Files:**
- Modify: `apps/academic/services.py:1172-1189`
- Modify: `apps/academic/tests/test_timetable.py` (extend `test_assign_substitution_notifies_substitute`)

**Interfaces:**
- Produces: the `SUBSTITUTE_ASSIGNED` `dispatch_intent` payload now includes `'type': 'SUBSTITUTE_ASSIGNED'` and `'substitution_id': <int>`, alongside its existing keys (`class_group`, `subject`, `date`, `period_no`, `original_teacher`).

- [ ] **Step 1: Write the failing test**

In `apps/academic/tests/test_timetable.py`, extend the existing
`test_assign_substitution_notifies_substitute` (currently at line 165) by
adding two assertions after the existing ones:

```python
    def test_assign_substitution_notifies_substitute(self):
        from apps.notifications.models import NotificationCategory, NotificationIntent

        sub = assign_substitution(self.slot, datetime.date(2026, 8, 3), self.substitute, reason="Sakit")

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.SUBSTITUTE_ASSIGNED,
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.recipient_user_id, self.substitute.user_id)
        self.assertEqual(intent.payload['class_group'], self.fx['class_group'].name)
        self.assertEqual(intent.payload['original_teacher'], self.fx['teacher'].person.full_name)
        self.assertEqual(intent.payload['type'], 'SUBSTITUTE_ASSIGNED')
        self.assertEqual(intent.payload['substitution_id'], sub.id)
```

(This replaces the existing test body — the only change is capturing
`assign_substitution(...)`'s return value as `sub` instead of discarding it,
and the two new assertion lines at the end.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.academic.tests.test_timetable.SubstitutionTests.test_assign_substitution_notifies_substitute -v 2`
Expected: FAIL with `KeyError: 'type'`

- [ ] **Step 3: Write minimal implementation**

In `apps/academic/services.py:1172-1189`, change:

```python
        dispatch_intent(
            foundation_id=slot.foundation_id,
            category=NotificationCategory.SUBSTITUTE_ASSIGNED,
            template_key='academic.substitution.assigned',
            payload={
                'class_group': slot.class_group.name,
                'subject': slot.class_subject.subject.name,
                'date': str(date),
                'period_no': str(slot.period_no),
                'original_teacher': original_teacher.person.full_name,
            },
```

to:

```python
        dispatch_intent(
            foundation_id=slot.foundation_id,
            category=NotificationCategory.SUBSTITUTE_ASSIGNED,
            template_key='academic.substitution.assigned',
            payload={
                'type': NotificationCategory.SUBSTITUTE_ASSIGNED,
                'substitution_id': substitution.id,
                'class_group': slot.class_group.name,
                'subject': slot.class_subject.subject.name,
                'date': str(date),
                'period_no': str(slot.period_no),
                'original_teacher': original_teacher.person.full_name,
            },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test apps.academic.tests.test_timetable -v 2`
Expected: PASS (full file, no regressions in neighboring tests)

- [ ] **Step 5: Commit**

```bash
git add apps/academic/services.py apps/academic/tests/test_timetable.py
git commit -m "feat(academic): add type/substitution_id to substitute-assigned payload

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Mobile — cold-start and tap-while-open notification handling

**Files:**
- Modify: `mobile/src/services/pushNotifications.ts`
- Test: `mobile/__tests__/pushNotifications.test.ts` (new)

**Interfaces:**
- Produces: `subscribeToNotificationResponseReceived(onData: (data: any) => void): { remove: () => void }` and `getInitialNotificationResponse(): Promise<any | null>`, both exported from `mobile/src/services/pushNotifications.ts`.

- [ ] **Step 1: Write the failing test**

Create `mobile/__tests__/pushNotifications.test.ts`:

```typescript
/**
 * Cold-start and tap-while-open push notification handling (spec/08 PAR-004).
 */
import { describe, it, mock } from 'node:test';
import assert from 'node:assert/strict';

describe('Push Notification Data Extraction', () => {
  it('subscribeToNotificationResponseReceived extracts data from a tap response', async () => {
    const calls: any[] = [];
    let capturedListener: ((response: any) => void) | null = null;

    // Reset module registry so our mock of expo-notifications is picked up fresh.
    delete require.cache[require.resolve('../src/services/pushNotifications.ts')];
    const Module = require('module');
    const originalRequire = Module.prototype.require;
    Module.prototype.require = function (id: string) {
      if (id === 'expo-notifications') {
        return {
          addNotificationResponseReceivedListener: (cb: (response: any) => void) => {
            capturedListener = cb;
            return { remove: () => {} };
          },
          getLastNotificationResponseAsync: async () => null,
          getPermissionsAsync: async () => ({ status: 'granted' }),
          requestPermissionsAsync: async () => ({ status: 'granted' }),
          addNotificationReceivedListener: () => ({ remove: () => {} }),
        };
      }
      return originalRequire.apply(this, arguments as any);
    };

    const { subscribeToNotificationResponseReceived } = require('../src/services/pushNotifications.ts');
    Module.prototype.require = originalRequire;

    const sub = subscribeToNotificationResponseReceived((data: any) => calls.push(data));
    assert.ok(capturedListener);
    capturedListener!({
      notification: { request: { content: { data: { type: 'ARRIVAL', student_id: 42, date: '2026-09-17' } } } },
    });

    assert.strictEqual(calls.length, 1);
    assert.deepStrictEqual(calls[0], { type: 'ARRIVAL', student_id: 42, date: '2026-09-17' });
    sub.remove();
  });

  it('getInitialNotificationResponse returns null when app was not launched by a notification', async () => {
    const { getInitialNotificationResponse } = require('../src/services/pushNotifications.ts');
    const result = await getInitialNotificationResponse();
    assert.strictEqual(result, null);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && node --test __tests__/pushNotifications.test.ts`
Expected: FAIL — `subscribeToNotificationResponseReceived is not a function`

- [ ] **Step 3: Write minimal implementation**

In `mobile/src/services/pushNotifications.ts`, add two new exports after
the existing `subscribeToNotificationReceived` function:

```typescript
export function subscribeToNotificationResponseReceived(
  onData: (data: any) => void
): { remove: () => void } {
  if (!Notifications || typeof Notifications.addNotificationResponseReceivedListener !== 'function') {
    return { remove: () => {} };
  }

  const subscription = Notifications.addNotificationResponseReceivedListener((response: any) => {
    const data = response?.notification?.request?.content?.data;
    if (data) {
      onData(data);
    }
  });

  return subscription;
}

export async function getInitialNotificationResponse(): Promise<any | null> {
  if (!Notifications || typeof Notifications.getLastNotificationResponseAsync !== 'function') {
    return null;
  }

  try {
    const response = await Notifications.getLastNotificationResponseAsync();
    return response?.notification?.request?.content?.data ?? null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && node --test __tests__/pushNotifications.test.ts`
Expected: PASS (2 tests)

Also run the full mobile suite to confirm no regressions:

Run: `cd mobile && npm test`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add mobile/src/services/pushNotifications.ts mobile/__tests__/pushNotifications.test.ts
git commit -m "feat(mobile): add cold-start and tap-while-open notification handling

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Mobile — pure deep-link resolution helpers

**Files:**
- Create: `mobile/src/services/deepLink.ts`
- Test: `mobile/__tests__/deepLink.test.ts` (new)

**Interfaces:**
- Consumes: `ChildSummary` and `AttendanceDayItem` types from `mobile/src/types/index.ts` (existing).
- Produces: `resolveDeepLinkChild(children: ChildSummary[], studentId: number | null | undefined): ChildSummary | null` and `findAttendanceRowIndex(days: AttendanceDayItem[], date: string | null | undefined): number` (returns `-1` if not found, matching `Array.prototype.findIndex`'s own convention).

- [ ] **Step 1: Write the failing test**

Create `mobile/__tests__/deepLink.test.ts`:

```typescript
/**
 * Pure deep-link resolution helpers for push-driven navigation (spec/08 PAR-004).
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { resolveDeepLinkChild, findAttendanceRowIndex } from '../src/services/deepLink.ts';
import type { ChildSummary, AttendanceDayItem } from '../src/types/index.ts';

describe('resolveDeepLinkChild', () => {
  const children: ChildSummary[] = [
    { student_id: 1, full_name: 'Anak Satu', photo_key: '', financial_responsible: true },
    { student_id: 2, full_name: 'Anak Dua', photo_key: '', financial_responsible: false },
  ];

  it('returns the matching child by student_id', () => {
    const result = resolveDeepLinkChild(children, 2);
    assert.strictEqual(result?.student_id, 2);
  });

  it('returns null when no child matches', () => {
    assert.strictEqual(resolveDeepLinkChild(children, 999), null);
  });

  it('returns null when studentId is null or undefined', () => {
    assert.strictEqual(resolveDeepLinkChild(children, null), null);
    assert.strictEqual(resolveDeepLinkChild(children, undefined), null);
  });
});

describe('findAttendanceRowIndex', () => {
  const days: AttendanceDayItem[] = [
    { id: 1, student: 1, date: '2026-09-15', status: 'HADIR', first_in_at: '07:00', first_out_at: null },
    { id: 2, student: 1, date: '2026-09-16', status: 'IZIN', first_in_at: null, first_out_at: null },
  ];

  it('returns the index of the row matching the date', () => {
    assert.strictEqual(findAttendanceRowIndex(days, '2026-09-16'), 1);
  });

  it('returns -1 when no row matches', () => {
    assert.strictEqual(findAttendanceRowIndex(days, '2026-01-01'), -1);
  });

  it('returns -1 when date is null or undefined', () => {
    assert.strictEqual(findAttendanceRowIndex(days, null), -1);
    assert.strictEqual(findAttendanceRowIndex(days, undefined), -1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && node --test __tests__/deepLink.test.ts`
Expected: FAIL — module `../src/services/deepLink.ts` not found

- [ ] **Step 3: Write minimal implementation**

Create `mobile/src/services/deepLink.ts`:

```typescript
/**
 * Pure resolution helpers for push-driven deep links (spec/08 PAR-004).
 * No side effects, no React/RN dependency — directly unit-testable.
 */
import type { AttendanceDayItem, ChildSummary } from '../types/index.ts';

export function resolveDeepLinkChild(
  children: ChildSummary[],
  studentId: number | null | undefined
): ChildSummary | null {
  if (studentId === null || studentId === undefined) return null;
  return children.find((c) => c.student_id === studentId) ?? null;
}

export function findAttendanceRowIndex(
  days: AttendanceDayItem[],
  date: string | null | undefined
): number {
  if (!date) return -1;
  return days.findIndex((d) => d.date === date);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && node --test __tests__/deepLink.test.ts`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add mobile/src/services/deepLink.ts mobile/__tests__/deepLink.test.ts
git commit -m "feat(mobile): add pure deep-link child/date resolution helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Mobile — wire `App.tsx` → `ParentShell` → `ParentAttendanceScreen`

**Files:**
- Modify: `mobile/App.tsx`
- Modify: `mobile/src/screens/parent/ParentShell.tsx`
- Modify: `mobile/src/screens/parent/ParentAttendanceScreen.tsx`

**Interfaces:**
- Consumes: `subscribeToNotificationResponseReceived`, `getInitialNotificationResponse` from `mobile/src/services/pushNotifications.ts` (Task 4). `resolveDeepLinkChild`, `findAttendanceRowIndex` from `mobile/src/services/deepLink.ts` (Task 5). `isParent` from `mobile/src/services/roleRouting.ts` (existing).

No dedicated test file for this task — it is wiring/glue only (all
branching logic it calls was already unit-tested in Tasks 4-5); verification
is the full existing mobile suite passing unchanged plus the manual test
plan in Step 4.

- [ ] **Step 1: Wire `App.tsx`**

Add the import after the existing `pushNotifications` import block (currently
lines 13-17):

```typescript
import {
  deactivatePushTokenAsync,
  registerForPushNotificationsAsync,
  subscribeToNotificationReceived,
  subscribeToNotificationResponseReceived,
  getInitialNotificationResponse,
} from './src/services/pushNotifications';
```

Add new state, right after the existing `const [nutritionMode, setNutritionMode] = useState(false);` line:

```typescript
  const [deepLinkChildId, setDeepLinkChildId] = useState<number | null>(null);
  const [deepLinkDate, setDeepLinkDate] = useState<string | null>(null);
```

Add a `handleNotificationData` function, defined before the bootstrap
`useEffect` (right after the `todayStr` line):

```typescript
  const handleNotificationData = (data: any, user: UserProfile | null) => {
    if (!data) return;
    if ((data.type === 'ARRIVAL' || data.type === 'DEPARTURE') && isParent(user)) {
      setParentTab('ATTENDANCE');
      setDeepLinkChildId(typeof data.student_id === 'number' ? data.student_id : null);
      setDeepLinkDate(typeof data.date === 'string' ? data.date : null);
    }
    // SUBSTITUTE_ASSIGNED: no `slot` object is included in the payload
    // anymore (see docs/superpowers/specs/2026-09-17-parent-app-push-deep-link-attendance-design.md
    // §4) — teachers land on the Agenda screen, already the default landing
    // screen, which surfaces pending substitutions on its own.
  };
```

Note this needs `isParent` imported (already imported at the top of
`App.tsx` per the existing `import { isParent } from './src/services/roleRouting';` line — confirm it's there; if not, add it).

Update the bootstrap effect's body — change:

```typescript
    const bootstrap = async () => {
      await initQueueDb();
      await initPosQueueDb();
      await initAnalyticsQueueDb();
      const authState = await checkAuth();
      if (authState.authenticated && authState.user) {
        setCurrentUser(authState.user);
        // Register push tokens in background
        registerForPushNotificationsAsync().catch(() => {});
        track('app_open');
      }
      setCheckingAuth(false);
    };

    bootstrap();

    // Subscribe to push notification events
    const sub = subscribeToNotificationReceived((notification) => {
      const data = notification?.request?.content?.data;
      if (data?.type === 'SUBSTITUTE_ASSIGNED' && data?.slot) {
        setSubModalSlot(data.slot);
      }
      track('notification_opened');
    });

    return () => {
      if (sub && typeof sub.remove === 'function') {
        sub.remove();
      }
    };
  }, []);
```

to:

```typescript
    let bootstrappedUser: UserProfile | null = null;

    const bootstrap = async () => {
      await initQueueDb();
      await initPosQueueDb();
      await initAnalyticsQueueDb();
      const authState = await checkAuth();
      if (authState.authenticated && authState.user) {
        setCurrentUser(authState.user);
        bootstrappedUser = authState.user;
        // Register push tokens in background
        registerForPushNotificationsAsync().catch(() => {});
        track('app_open');

        // Cold-start: app was launched by tapping a notification.
        const initialData = await getInitialNotificationResponse();
        if (initialData) {
          handleNotificationData(initialData, bootstrappedUser);
        }
      }
      setCheckingAuth(false);
    };

    bootstrap();

    // Foreground: notification arrived while the app is already open.
    const sub = subscribeToNotificationReceived((notification) => {
      const data = notification?.request?.content?.data;
      if (data?.type === 'SUBSTITUTE_ASSIGNED' && data?.slot) {
        setSubModalSlot(data.slot);
      }
      track('notification_opened');
    });

    // Tap: user taps a notification while the app is backgrounded or foregrounded.
    const responseSub = subscribeToNotificationResponseReceived((data) => {
      handleNotificationData(data, bootstrappedUser);
      track('notification_opened');
    });

    return () => {
      if (sub && typeof sub.remove === 'function') {
        sub.remove();
      }
      if (responseSub && typeof responseSub.remove === 'function') {
        responseSub.remove();
      }
    };
  }, []);
```

`bootstrappedUser` is a plain closure variable (not React state) because the
tap listener is registered once in this same effect and needs the
user resolved at bootstrap time without waiting on a state update's render
cycle — `currentUser` state may not be committed yet when a tap fires very
early after launch. Once `currentUser` state itself updates,
`bootstrappedUser` and `currentUser` are the same value going forward.

Finally, pass the new deep-link props into `ParentShell`'s child function
where it currently renders `ParentAttendanceScreen` — change:

```typescript
            ) : parentTab === 'ATTENDANCE' ? (
              <ParentAttendanceScreen child={selectedChild} />
```

to:

```typescript
            ) : parentTab === 'ATTENDANCE' ? (
              <ParentAttendanceScreen child={selectedChild} highlightDate={deepLinkDate} />
```

and pass `deepLinkChildId` into the `<ParentShell>` element itself — change:

```typescript
        <ParentShell activeTab={parentTab} onTabChange={setParentTab} onLogout={handleLogout}>
```

to:

```typescript
        <ParentShell
          activeTab={parentTab}
          onTabChange={setParentTab}
          onLogout={handleLogout}
          deepLinkChildId={deepLinkChildId}
        >
```

- [ ] **Step 2: Wire `ParentShell.tsx`**

Add the import after the existing `track` import:

```typescript
import { resolveDeepLinkChild } from '../../services/deepLink.ts';
```

Add `deepLinkChildId` to the props interface:

```typescript
interface ParentShellProps {
  activeTab: ParentTab;
  onTabChange: (tab: ParentTab) => void;
  onLogout: () => void;
  deepLinkChildId?: number | null;
  children: (ctx: { selectedChild: ChildSummary; allChildren: ChildSummary[] }) => React.ReactNode;
}
```

Destructure it in the component signature:

```typescript
export const ParentShell: React.FC<ParentShellProps> = ({ activeTab, onTabChange, onLogout, deepLinkChildId, children }) => {
```

Add a new effect right after the existing PAR-017 effect (which ends with
`}, [selectedChild, activeTab, onTabChange]);`):

```typescript
  // PAR-004: a push-driven deep link names a specific child; switch to it
  // once the child list has loaded, covering both "already viewing this
  // child" (no-op) and "switch to a different child" cases.
  useEffect(() => {
    if (allChildren.length === 0 || deepLinkChildId === undefined || deepLinkChildId === null) return;
    const target = resolveDeepLinkChild(allChildren, deepLinkChildId);
    if (target && target.student_id !== selectedId) {
      handleSelectChild(target.student_id);
    }
  }, [allChildren, deepLinkChildId]);
```

- [ ] **Step 3: Wire `ParentAttendanceScreen.tsx`**

Add `useRef` to the existing React import:

```typescript
import React, { useEffect, useRef, useState } from 'react';
```

Add the import after the existing `attendanceStatusLabel` import:

```typescript
import { findAttendanceRowIndex } from '../../services/deepLink.ts';
```

Add `highlightDate` to the props interface:

```typescript
interface ParentAttendanceScreenProps {
  child: ChildSummary;
  highlightDate?: string | null;
}
```

Destructure it in the component signature:

```typescript
export const ParentAttendanceScreen: React.FC<ParentAttendanceScreenProps> = ({ child, highlightDate }) => {
```

Add a `FlatList` ref right after the existing state declarations (after the
`submitting` state line):

```typescript
  const flatListRef = useRef<FlatList<AttendanceDayItem>>(null);
```

Add an effect that scrolls to and highlights the target row once `days` has
loaded, right after the existing data-loading `useEffect` (the one ending
`}, [child.student_id, activeTab]);`):

```typescript
  useEffect(() => {
    if (activeTab !== 'TIMELINE' || !highlightDate || days.length === 0) return;
    const index = findAttendanceRowIndex(days, highlightDate);
    if (index === -1) return;
    // scrollToIndex can throw if the target row hasn't been measured/laid
    // out yet on a long list — harmless to skip in that case, the row is
    // still visually highlighted below even if not auto-scrolled to.
    try {
      flatListRef.current?.scrollToIndex({ index, animated: true, viewPosition: 0.3 });
    } catch {
      // ignore
    }
  }, [days, highlightDate, activeTab]);
```

Attach the ref and highlight style to the existing `FlatList` in the
`TIMELINE` branch — change:

```typescript
        <FlatList
          data={days}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>Belum ada data riwayat presensi.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <View style={styles.row}>
              <View style={[styles.dot, { backgroundColor: STATUS_COLOR[item.status] ?? colors.muted }]} />
              <View style={styles.rowText}>
                <Text style={styles.rowDate}>{item.date}</Text>
                <Text style={styles.rowStatus}>
                  {attendanceStatusLabel(item.status)}
                  {item.first_in_at ? ` — Tiba ${item.first_in_at}` : ''}
                </Text>
              </View>
            </View>
          )}
        />
```

to:

```typescript
        <FlatList
          ref={flatListRef}
          data={days}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          onScrollToIndexFailed={() => {}}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>Belum ada data riwayat presensi.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <View style={[styles.row, item.date === highlightDate && styles.rowHighlighted]}>
              <View style={[styles.dot, { backgroundColor: STATUS_COLOR[item.status] ?? colors.muted }]} />
              <View style={styles.rowText}>
                <Text style={styles.rowDate}>{item.date}</Text>
                <Text style={styles.rowStatus}>
                  {attendanceStatusLabel(item.status)}
                  {item.first_in_at ? ` — Tiba ${item.first_in_at}` : ''}
                </Text>
              </View>
            </View>
          )}
        />
```

Add the `rowHighlighted` style to the `StyleSheet.create` block, right after
the existing `row: {...}` style:

```typescript
  rowHighlighted: {
    borderColor: colors.primary,
    borderWidth: 2,
    backgroundColor: colors.surfaceAlt,
  },
```

- [ ] **Step 4: Manual verification**

No RN component test harness exists in this repo, so this task's wiring is
verified manually rather than by an automated component test (the logic it
calls was already covered in Tasks 4-5). Start the Expo dev server
(`cd mobile && npx expo start`), sign in as a guardian, and:

1. Confirm the app still opens normally with no deep link (existing
   behavior unchanged: Home tab, no crash from the new optional props being
   `undefined`).
2. Manually verify the `handleNotificationData` function's branching by
   temporarily invoking it from a debug button or the RN debugger's console
   with a sample `{type: 'ARRIVAL', student_id: <a real child's id>, date: '<a real date from that child's attendance>'}` payload — confirm the app switches to the Attendance tab, switches the child switcher to the right child, and scrolls to/highlights the matching row.
3. Confirm passing `undefined`/`null` for `highlightDate`/`deepLinkChildId`
   (the normal non-deep-link case) causes no visible change to the existing
   Attendance screen or child switcher.

- [ ] **Step 5: Run the full test suite**

Run: `cd mobile && npm test`
Expected: PASS (all existing tests unaffected — this task only adds new
optional props with default no-op behavior when absent)

- [ ] **Step 6: Commit**

```bash
git add mobile/App.tsx mobile/src/screens/parent/ParentShell.tsx mobile/src/screens/parent/ParentAttendanceScreen.tsx
git commit -m "feat(mobile): deep-link push taps into the correct child's attendance date

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

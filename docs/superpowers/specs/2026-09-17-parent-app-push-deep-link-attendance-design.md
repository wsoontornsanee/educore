# Parent App: Push-Driven Deep Link into Attendance Detail — Design

**Source:** Notion [Open Item] Parent App: Push-Driven Deep Link into Attendance Detail
(deferred from Step 10.1). `spec/08-parent-app.md` PAR-004: "Arrival/departure
notifications MUST deep-link into the attendance screen for that date."

## Scope

The Notion note frames this as "deep-link payload/navigation wiring" assuming
push delivery infra already exists. It mostly doesn't: DEPARTURE dispatches
nothing at all, no code path anywhere populates a structured push data
payload, and the mobile app has zero cold-start (tap-to-open) notification
handling. This design builds all three, reusing existing plumbing wherever
possible rather than adding new infrastructure.

**Out of scope, deliberately:** a real Expo/FCM push-send integration.
Every notification channel in this codebase (`WhatsAppCloudApiProvider` is
the one partial exception) is a `Mock*Provider` — push delivery is
architecturally deferred platform-wide, not a gap specific to this task.
This design makes the data payload correct and inspectable through the
existing mock, ready for whenever real delivery lands.

**Also out of scope:** rebuilding the teacher-side "tap opens the
substitution review modal" flow (see the SUBSTITUTE_ASSIGNED section below)
— logged as its own Notion Open Item instead.

## Backend

**No new model field.** `dispatch_intent(...payload=...)` already stores the
payload on `NotificationIntent.payload` and passes it to every provider as
`provider.send(..., variables=intent.payload, ...)` (`apps/notifications/services.py:415`).
The payload dict already *is* the future push data payload — the only gap is
`MockPushProvider.send()` (`apps/notifications/providers.py:120-155`) never
records the `variables` it receives. Fix: capture `variables` into each
entry of `dispatched_messages`, matching how `MockWhatsAppProvider` already
does (`apps/notifications/providers.py:95`).

**Payload shape.** Each dispatch call site adds a `type` key (the notification
category as a plain string) plus the minimum id the mobile client needs:

- ARRIVAL / DEPARTURE: `{'type': 'ARRIVAL' | 'DEPARTURE', 'student_id': <int>, 'date': <'YYYY-MM-DD'>}`
- SUBSTITUTE_ASSIGNED: `{'type': 'SUBSTITUTE_ASSIGNED', 'substitution_id': <int>}`

Adding new keys to a payload dict is safe: `NotificationTemplate` bodies do
`{variable}` substitution against whatever keys are present — an unused key
is simply never referenced, no existing template needs to change.

**DEPARTURE dispatch.** `apps/attendance/services.py`'s `ingest_gate_events`
currently only calls `handle_gate_scanned_event` when `direction == 'IN'`
(`apps/attendance/services.py:509-514`). Add the mirror call for
`direction == 'OUT'`. `handle_gate_scanned_event`
(`apps/notifications/services.py:504`) gets a `category` parameter (default
`NotificationCategory.ARRIVAL`, passed explicitly as `DEPARTURE` from the new
call site) instead of being ARRIVAL-only; the guardian fan-out loop,
try/except-never-blocks-the-gate-scan guard, and dedupe-key pattern
(`f"{category.lower()}:{student.id}:{date}:{guardian.id}"`) are otherwise
identical between the two directions.

**student_id in the payload.** The current ARRIVAL payload
(`apps/notifications/services.py`, inside `handle_gate_scanned_event`) has
`student_name`/`date`/etc but never `student_id` — add it; the function
already resolves the `Student` row to build `student_name`.

**SUBSTITUTE_ASSIGNED payload.** `apps/academic/services.py:1172-1189`'s
`dispatch_intent(...payload={...})` call gains `'type': 'SUBSTITUTE_ASSIGNED'`
and `'substitution_id': substitution.id` (already in scope at that call
site) alongside its existing keys.

## Mobile

**`mobile/src/services/pushNotifications.ts`** gains two exports:
- `subscribeToNotificationResponseReceived(cb: (data: any) => void)` —
  wraps `Notifications.addNotificationResponseReceivedListener`, fires when
  the user taps a notification while the app is foregrounded or
  backgrounded (not fully closed). Extracts
  `response.notification.request.content.data` and calls `cb` with it,
  returning the subscription for cleanup — same shape as the existing
  `subscribeToNotificationReceived`.
- `getInitialNotificationResponse(): Promise<any | null>` — wraps
  `Notifications.getLastNotificationResponseAsync()`, returns the `data`
  object if the app was launched by tapping a notification (the cold-start
  case), or `null` otherwise.

**`App.tsx`** adds a single `handleNotificationData(data)` function, called
from two places: the new `subscribeToNotificationResponseReceived` (tap)
listener, and a one-time `getInitialNotificationResponse()` check in the
bootstrap effect (after auth resolves, since deep-linking only makes sense
once we know who's signed in and whether they're staff or parent). It is
deliberately NOT called from the existing foreground
`subscribeToNotificationReceived` listener: that listener fires on mere
delivery, before any user interaction, and silent delivery must never
auto-navigate — a parent mid-task who receives a push for a sibling should
not be yanked to another tab with no tap. Cold-start via
`getInitialNotificationResponse()` is still tap-driven in effect, since the
app was launched by the user tapping the notification that produced it.

`handleNotificationData`:
- `data.type === 'SUBSTITUTE_ASSIGNED'`: the existing `setSubModalSlot(data.slot)`
  branch is left untouched but is now unreachable in practice, since the new
  payload never includes a `slot` object (see Scope) — teachers land on the
  Agenda screen instead, already the default landing screen, which surfaces
  pending substitutions on its own. The follow-up Open Item covers building
  a real fetch-by-id path to restore the auto-opened modal.
- `data.type === 'ARRIVAL' | 'DEPARTURE'` and the signed-in user is a
  parent: calls `setParentTab('ATTENDANCE')` and sets new state
  `deepLinkChildId`/`deepLinkDate` (from `data.student_id`/`data.date`),
  passed down as props through `ParentShell` to `ParentAttendanceScreen`.

**`ParentShell.tsx`** gains an optional `deepLinkChildId?: number` prop and
an effect: when it changes and names a `student_id` present in
`allChildren`, calls the existing `handleSelectChild` to switch to it —
covers both "already viewing this child" (no-op) and "switch to a
different child" (drives the existing child-switcher machinery, including
its PAR-017 financial-visibility re-check).

**`ParentAttendanceScreen.tsx`** gains an optional `highlightDate?: string`
prop: on mount/update, scrolls its list to the row matching that date and
applies a highlighted style. No auto-clear timer needed — a new deep link
or navigating away naturally supersedes it.

## Testing

- Backend: `apps/notifications/tests/` — `MockPushProvider.send()` now
  records `variables`; a new test confirms `dispatched_messages[-1]['variables']`
  equals what was passed. `apps/attendance/tests/` — a gate scan with
  `direction='OUT'` dispatches a DEPARTURE notification per linked guardian
  (mirroring the existing ARRIVAL test), with the correct payload shape
  (`type`, `student_id`, `date`) and cross-foundation isolation matching the
  existing ARRIVAL test's pattern. `apps/academic/tests/` — the existing
  substitution-assignment test gains an assertion on the new payload keys.
- Mobile: `mobile/__tests__/` — `pushNotifications.test.ts` (new) covers
  `subscribeToNotificationResponseReceived` and
  `getInitialNotificationResponse`'s data-extraction logic against mocked
  Expo Notifications responses. `ParentShell`'s deep-link-child-switch effect
  and `ParentAttendanceScreen`'s highlight-and-scroll-to-date logic get
  targeted unit tests following this app's existing `node --test` style.

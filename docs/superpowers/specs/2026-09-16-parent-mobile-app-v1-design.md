# Parent Mobile App v1 (Step 10.1) — Design

Notion: "Step 10.1 — Parent Mobile App v1: Login, Attendance, Invoicing & VA Pay" (page id `3dc347a6-6594-810d-a66b-c77fbbaad20d`, currently labeled `TASK-032` in its title — that ID is already used by "POS Checkout" in `memory/01_PROJECT.md`; this task will be logged under the next free ID, e.g. `TASK-075`, when memory/Notion are updated post-merge).

Spec references: `spec/08-parent-app.md` (`PAR-001` to `PAR-020`), `spec/00-overview.md §6`, `spec/02-identity-and-access.md` (`IAM-002`, `IAM-003`, `IAM-009`, `IAM-014`).

## 1. Scope

In scope for this slice:
- Phone + OTP login for guardians (`PAR-001`).
- Child switcher, remembers last selection (`PAR-003`).
- Minimal Home: per-child status card + entry points into Attendance/Payments (subset of `PAR-002`).
- Attendance: calendar/timeline view for the selected child (subset of spec/08 §2 "Attendance").
- Payments: outstanding invoice list, pay via VA/QRIS, step-by-step instructions + countdown, non-financial-guardian tab hiding (`PAR-005`, `PAR-006`, `PAR-007`, `PAR-017`).
- Basic offline degrade: cached last-loaded data with staleness stamp, writes disabled offline (`PAR-015`).

Explicit non-goals for this slice (each gets its own Notion `[Open Item]` per AGENTS.md §4 protocol):
- Wallet tab (balance, top-up, spend controls) — spec/08 §2 "Wallet".
- Academic tab (grades, homework, report cards, timetable) — spec/08 §2 "Academic".
- Messages tab (announcements, teacher messages, permission slips) — spec/08 §2 "Messages".
- Profile tab: notification preferences, language switch (`PAR-013`, `PAR-014`), biometric unlock (`PAR-018`).
- Absence request with photo attachment (`PAR-011`).
- Permission slip digital acknowledgement (`PAR-012`).
- Receipts as downloadable/shareable PDF (`PAR-009`).
- Push-driven deep link from notification into attendance detail (`PAR-004`) — depends on payload wiring, deferred.
- Analytics event instrumentation (spec/08 §5).

## 2. Backend changes

All new code lives in `apps.identity` (auth) — Attendance/Invoice/Payment already have guardian-scoped endpoints from TASK-073/TASK-055/061 and need no changes.

### 2.1 OTP login endpoints

- `POST /api/v1/auth/otp/request/`
  - Body: `{"phone_e164": "+62..."}`.
  - Calls existing `apps.identity.services.request_phone_otp(phone)`. That function already enforces the 3-sends/15-minute throttle (`IAM-002`/`IAM-003`) and returns `(challenge, code)` — the endpoint discards the raw code (delivery is a stub/log today, same as the rest of the OTP infra; no new SMS/WhatsApp provider work in this slice) and returns `{"challenge_id": challenge.id}`.
  - No auth required (public, like the existing token-obtain endpoint).

- `POST /api/v1/auth/otp/verify/`
  - Body: `{"challenge_id": 123, "code": "123456"}`.
  - Calls `verify_phone_otp(challenge_id, code)`. On failure, returns `400` with the Indonesian message the service already produces (expired / already used / too many attempts / wrong code).
  - On success, looks up `User.objects.get(phone_e164=challenge.phone_e164)`. If no such `User` exists, returns `404 {"code": "GUARDIAN_NOT_REGISTERED"}` — this slice does not auto-provision a `User`/`Guardian` from a bare phone number; a guardian must already have been linked to a student via the existing `POST /students/:id/guardians/` flow (which already creates the `User` row, see `apps/identity/views.py` guardian-link handler).
  - On success, issues the same JWT pair shape as `EduCoreTokenObtainPairView` (reuse `RefreshToken.for_user(user)` / the same serializer output the existing token view returns) plus the `UserProfileSerializer` payload, so mobile's existing `saveTokens`/`saveUserProfile` code works unmodified.
  - Fail-closed: if the resolved `User` has no active `RoleAssignment` with `role=parent` anywhere, still issue the token (a guardian's RBAC is enforced downstream by `apps.identity.guardian_access`, not by `RoleAssignment` — confirm against TASK-073's implementation before assuming; if TASK-073 actually keys off `RoleAssignment(role=parent)`, this endpoint must create one lazily the same way guardian creation should already do it — resolve during implementation by reading `guardian_access.py`, not by guessing here).

### 2.2 Child list endpoint

- `GET /api/v1/me/children/`
  - Auth required. Uses `apps.identity.guardian_access.get_guardian_student_ids(request.user)` (existing) to resolve the caller's students, then returns per child: `{student_id, full_name, photo_key, financial_responsible}`. `financial_responsible` comes from the `GuardianLink` row for that `(guardian, student)` pair — drives `PAR-017` (hide Payments entirely for a non-financial guardian) and the child switcher list.
  - If the caller is a `Staff` user (not a guardian), returns `403` — this endpoint is guardian-only, mirroring the existing `is_staff_user` check pattern in `guardian_access.py`.

### 2.3 Reused, unchanged

- `AttendanceDayViewSet` (attendance app) — already 3-layer-tenant + guardian-filtered.
- `InvoiceViewSet` (finance app) — already filters out amounts for non-financial guardians per TASK-073.
- `create_payment_intent` / `PaymentIntentSerializer` (finance app, TASK-055/056/061) — already computes convenience fee, returns VA/QRIS instructions.

## 3. Mobile changes (`mobile/`, bolt-on to existing `educore-guru` Expo app)

No new Expo project. `App.tsx` keeps the existing teacher tree entirely intact; a guardian login branches into a new, parallel tree.

### 3.1 Login

- `LoginScreen` gains a role toggle: `Guru` (existing password form, untouched) / `Wali Murid` (new: phone entry → OTP code entry, two-step within the same screen component).
- New `mobile/src/services/parentAuth.ts`: `requestOtp(phone)`, `verifyOtp(challengeId, code)` — calls the two new endpoints, then reuses existing `saveTokens`/`saveUserProfile` from `storage.ts` exactly like `auth.ts`'s `login()` does today.

### 3.2 Routing

- `App.tsx`: after `checkAuth()`/login resolves a user, branch on `currentUser.role`. `teacher`/`staff` → existing `AgendaScreen` flow (zero changes). `parent` → new `ParentShell` component: a persistent child-switcher header + bottom tab bar with `Home` / `Absensi` / `Tagihan` tabs, hiding `Tagihan` when `financial_responsible` is false for every linked child that has it false and the child currently selected lacks it (per-child, since `PAR-017` is per guardian-per-student).
- Last-selected child persisted via existing `storage.ts` (`AsyncStorage`/`SecureStore` pattern already used for tokens) keyed `parent.lastChildId`.

### 3.3 New screens/services

- `ParentHomeScreen`: status card for the selected child (state enum from spec/08 §4: `Sudah di sekolah` / `Belum tiba` / `Sudah pulang` / `Sakit` / `Izin` / `Tidak hadir` / `Libur`, derived from the latest `AttendanceDay` row) + outstanding-invoice summary line (hidden if non-financial) + a "Pay" CTA per `PAR-005`/`PAR-006` quick-pay rule (single outstanding invoice → pays directly; multiple → opens invoice list, oldest pre-selected).
- `ParentAttendanceScreen`: calendar/list of `AttendanceDay` rows for the selected child, arrival/departure timestamps.
- `ParentInvoicesScreen`: outstanding + paid invoice list for the selected child (finance `InvoiceViewSet`).
- `PaymentScreen`: shows base amount / convenience fee / total before confirming (`PAR-006`), then after `create_payment_intent`, VA/QRIS instructions with a copyable VA number and expiry countdown (`PAR-007`). Payment confirmation arriving as a push (`PAR-008`) reuses the existing `DevicePushToken` infra from TASK-M4 — no new push plumbing, just a new notification-opened handler in `App.tsx`'s existing `subscribeToNotificationReceived` callback that refreshes the invoice list when a payment-settled push lands.
- New services: `mobile/src/services/children.ts`, `attendance.ts` (parent-facing read), `invoices.ts`, `payments.ts` — thin wrappers over `apiClient`, mirroring the existing `agenda.ts` service's shape.

### 3.4 Offline degrade (`PAR-015`)

- `ParentHomeScreen`/`ParentAttendanceScreen`/`ParentInvoicesScreen` each cache their last successful response via `storage.ts` (new keys, same pattern as existing token/profile caching) and render from cache with a "Terakhir diperbarui: <timestamp>" stamp when a fetch fails. Reuses the existing `StaleOfflineBanner` component. The `PaymentScreen`'s pay action is disabled with an explanatory inline message when offline — no network call attempted, not a caught exception after the fact.

## 4. Data flow (happy path: pay an invoice)

1. Guardian logs in via OTP → JWT stored.
2. `GET /me/children/` → child switcher populated, last-selected child restored from storage (or first child on first login).
3. `ParentHomeScreen` loads: `AttendanceDayViewSet` (today) + `InvoiceViewSet` (outstanding, filtered by `financial_responsible`).
4. Guardian taps Pay → single outstanding invoice → `PaymentScreen` → `create_payment_intent` → VA/QRIS instructions shown.
5. Guardian completes transfer externally (bank app) → existing Xendit webhook (`process_payment_webhook`, unchanged) settles the invoice.
6. Existing push-notification pipeline (unchanged) fires a payment-confirmed push → parent app's push handler refreshes the invoice list.

## 5. Error handling

- OTP request/verify: surfaces the existing Indonesian error strings from `apps.identity.services` verbatim (rate limit, expired, wrong code, too many attempts).
- `GET /me/children/` for a phone with no linked students: returns `200` with an empty list; mobile shows an explicit "Tidak ada data anak terhubung" empty state, not a crash.
- Payment intent creation failure (e.g. gateway timeout): surfaced as a retryable error banner on `PaymentScreen`, invoice list unchanged (no optimistic state).
- Network failure on any GET: falls back to cached data per §3.4; on any POST (pay): blocked outright, not silently retried.

## 6. Testing

- Django (`apps/identity/tests/`): OTP request throttling reuses existing service-level tests; new tests for the two view endpoints — happy path, wrong/expired code, unregistered phone (404), cross-tenant isolation on `/me/children/`, non-guardian `403` on `/me/children/`, `financial_responsible` correctly per-child.
- Mobile (`mobile/__tests__/`): `parentAuth.test.ts` (mirrors existing `auth.test.ts`), a child-switch/last-selected-persistence test, and an offline-cache-render test for the invoice/attendance screens.
- Target ≥80% coverage on new business logic per AGENTS.md §4.

## 7. Open decisions carried forward (not blocking this slice)

- Whether `verify_phone_otp` success should lazily create a `RoleAssignment(role=parent)` if one doesn't already exist — resolve by reading `apps.identity.guardian_access` during implementation, not guessed here (see §2.1).
- OTP delivery channel (WhatsApp/SMS provider) is out of scope — `request_phone_otp` already stubs delivery the same way the rest of the notifications provider stack does; wiring a real provider is tracked separately if not already covered by an existing Open Item.

# Frontend / Mobile Delivery Plan

Source: design handoff `design_handoff_frontend_mobile/` (README.md, `designs/*.dc.html`,
`specs/`). Backlog: ACD-030, ACD-006, TCH-007, TCH-005/006/014, ACD-019, ACD-024.

## 1. Repo audit — what the client layer builds on

**Backend is a single Django 5.1 project, DRF-only today. No template, no static
asset, no JS tooling exists.**

- `apps/` (12 apps): `academic`, `attendance`, `campus`, `core`, `finance`,
  `foundation`, `hardware`, `identity`, `notifications`, `reporting`, `wallet`.
- Routing: [educore/urls.py](../educore/urls.py) — every route mounted under
  `/api/v1/...`, `include()`d per app. No non-API route exists.
- Templates: `TEMPLATES[0]['DIRS'] = [BASE_DIR / 'frontend' / 'templates']`
  ([educore/settings/base.py:88](../educore/settings/base.py)) — **the directory
  does not exist yet**, but the setting already anticipates it.
- Static: `STATIC_ROOT = frontend/collected_static`,
  `STATICFILES_DIRS = [frontend/static] if exists else []`
  ([educore/settings/base.py:155-157](../educore/settings/base.py)) — same,
  anticipated, not built.
- `django_htmx` is already in `INSTALLED_APPS`
  ([educore/settings/base.py:51](../educore/settings/base.py)) but unused —
  no template renders, no view checks `request.htmx`.
- Auth: `DEFAULT_AUTHENTICATION_CLASSES = [JWTAuthentication, SessionAuthentication]`
  ([educore/settings/base.py:178-181](../educore/settings/base.py)), backed by
  `apps.identity.backends.DualAuthBackend` (phone or email login, lockout
  policy) plus stock `ModelBackend`. Both session (cookie, for server-rendered
  web) and JWT (bearer, for a native mobile app) are already wired.
- RBAC: `apps.identity.rbac.is_foundation_admin` /
  `ROLE_PERMISSIONS` matrix ([apps/identity/rbac.py](../apps/identity/rbac.py)),
  enforced per-view via `HasRequiredPermission`
  ([apps/identity/permissions.py:13](../apps/identity/permissions.py)) and each
  `ViewSet.action_permissions` dict. This is what every new view must reuse —
  no parallel authorization scheme.
- Tenancy: `TenantModel` / `TenantManager` at
  [apps/core/models.py:12](../apps/core/models.py), foundation scoping via
  `get_current_foundation_id()` (`educore/middleware/tenancy.py`), enforced in
  every `ViewSet.get_queryset()` already audited (`TenantScopedModelViewSet`,
  [apps/academic/views.py:131](../apps/academic/views.py)).
- Pagination: `StandardCursorPagination` (`ordering='-created_at'`, `page_size
  50`) at [apps/core/pagination.py](../apps/core/pagination.py) — matches the
  handoff's `?cursor=&limit=` → `{data, next_cursor}` contract in shape but
  DRF's cursor paginator names the field `results`, not `data`, and there is no
  `next_cursor` key — it returns `next`/`previous` full URLs. **Confirm the
  client accepts DRF's native cursor envelope, or add a thin response
  wrapper** (Decision #4 below).
- Money: `apps.core.fields.MoneyField` (`DECIMAL(18,2)`), `COERCE_DECIMAL_TO_STRING
  = True` in DRF settings — every API money value is already a JSON string, safe
  to hand straight to `Intl.NumberFormat` / manual IDR grouping. No client-side
  decimal math needed.
- File storage — **gap**: spec `ARC-026`/`ARC-030` (`specs/01-platform-architecture.md:181,185`)
  requires every uploaded object to be a `core.StoredFile` row, uploaded
  client-to-GCS via signed URL, never touching server disk. **No `StoredFile`
  model, no signed-URL endpoint, and no GCS client exist anywhere in `apps/`.**
  The one file-upload path in scope for these six items,
  `HomeworkViewSet.upload_file` → `store_homework_submission_file`
  ([apps/academic/services.py:542](../apps/academic/services.py)), writes
  straight to `MEDIA_ROOT` via `pathlib` — the docstring says so itself,
  citing it as the existing convention. None of the six backlog items need
  file upload directly, so this is out of scope here, but it blocks anything
  that does (submissions with attachments) and contradicts the spec /
  README's "never written to server disk" rule. **Logging as a separate
  Notion Open Item, not fixing in this plan.**
- Idempotency — **gap**: `apps.core.models.IdempotencyRecord`
  ([apps/core/models.py:161](../apps/core/models.py)) exists as a table but is
  referenced nowhere outside its own migration — no middleware or DRF mixin
  reads an `Idempotency-Key` header anywhere. `apps/wallet` has its own
  separate per-model `idempotency_key` field, unrelated to this table. The
  handoff's "every write endpoint accepts `Idempotency-Key`" is true of no
  endpoint today. This directly affects two in-scope items: exam answer
  autosave (POST every ≤20s) and the offline period-attendance sync queue.
  **Must build**: a generic DRF mixin/decorator that hashes `(endpoint, key,
  request body)` against `IdempotencyRecord` and replays the stored response
  on a key collision. Foundation-layer work, done once, used by both.
- Push notifications: `ChannelType.PUSH` / `IN_APP` exist as enum values
  ([apps/notifications/models.py:12-17](../apps/notifications/models.py)), and
  `NotificationCategory.HOMEWORK` is already configured with the right default
  channels. But `apps/notifications/providers.py` — **check before assuming a
  device-token registration endpoint exists; none was found in `apps/notifications/urls.py`.**
  A mobile app needs somewhere to register its push token; this is missing.
- Substitution decline — **gap**: `TimetableSubstitution` creation
  (`assign_substitution`) exists, but no accept/decline action was found
  anywhere in `apps/academic` or `apps/attendance` — the Teacher Mobile Agenda
  design's "Tidak bisa mengajar" (decline, returns to assigning admin with a
  reason) has no backend counterpart yet.
- `deploy/crontab` — 24 scheduled commands, all fired via
  `apps.core.management.base.CronHostCommand` (single dedicated cron host per
  `spec/01 §7`). Nothing here needs a new cron job for the six items; the
  read-facing agenda/gradebook/completion-bar screens read live tenant tables,
  not a `rpt_*` rollup.

**What already exists and is directly reusable per item** (no new backend
needed beyond the two gaps above):

| Item | Existing endpoint(s) |
|---|---|
| Gradebook grid (ACD-006, TCH-005/006) | `GET /api/v1/academic/gradebook/?class_subject_id=&term_id=` ([apps/academic/views.py:867](../apps/academic/views.py)); `PUT /api/v1/academic/assessments/:id/scores/` bulk cell write with `expected_version` optimistic-lock ([apps/academic/views.py:248](../apps/academic/views.py)) |
| Merge prompt (TCH-007) | Same `scores` endpoint already returns `409` + `{conflict: {current_score, current_version, current_feedback, current_descriptor}}` on stale `expected_version` ([apps/academic/views.py:274-284](../apps/academic/views.py)) — **the conflict data contract is complete**, only the modal UI is new |
| Grading queue (TCH-014) | `GET /api/v1/academic/exams/:id/grading-queue/` ([apps/academic/views.py:532](../apps/academic/views.py)); homework side via `HomeworkSubmissionViewSet.grade`/`return_submission` ([apps/academic/views.py:463,476](../apps/academic/views.py)) |
| Homework completion bar (ACD-030) | `GET /api/v1/academic/homework/:id/completion/` ([apps/academic/views.py:446](../apps/academic/views.py)); `POST .../remind/` with `429` rate-limit response ([apps/academic/views.py:437](../apps/academic/views.py)); `GET .../submissions/` |
| Teacher mobile agenda (ACD-019) | `GET /api/v1/teacher/agenda?date=` ([apps/attendance/views.py:829](../apps/attendance/views.py)); `POST /api/v1/timetable/slots/:id/period-attendance/` ([apps/attendance/views.py:853](../apps/attendance/views.py)); `POST /api/v1/period-attendance/sync/` for offline batch replay ([apps/attendance/views.py:898](../apps/attendance/views.py)) |
| Exam lockdown (ACD-024) | `POST /api/v1/academic/exam-attempts/:id/answers/`, `.../submit/`, `.../focus-loss/` ([apps/academic/views.py:567,589,595](../apps/academic/views.py)); server-side `auto_submit_expired_exam_attempts` cron already force-submits on window expiry |

## 2. Client stack — confirmed, not overridden

The design assumes Django templates + HTMX + Tailwind (web) and React Native
(mobile). The repo evidence supports exactly this, with two adjustments:

- **Web: Django templates + HTMX + Tailwind — confirmed.** `django_htmx` is
  already installed; `TEMPLATES`/`STATICFILES_DIRS` already point at a
  `frontend/` tree that just needs to be created. This avoids a second
  service, a second auth scheme, a build-and-deploy split, or CORS — all
  forbidden or unnecessary per the platform constraints. Tailwind ships via
  its CLI (no Node runtime requirement at request time — compile at build/CI
  time into `frontend/static/css/app.css`, checked into `STATICFILES_DIRS`
  output the way `collectstatic` expects). `SessionAuthentication` +
  Django's CSRF middleware (already default) is what these views will use;
  HTMX sends the CSRF cookie back via the standard `hx-headers` /
  meta-tag pattern — no new package needed.
- **Mobile: React Native — confirmed, with `JWTAuthentication` already in
  place as its auth path.** No React Native project exists yet
  (`package.json` absent everywhere in the repo); this is greenfield, to be
  created as `mobile/` at repo root, **not** inside the Django project.
- **Adjustment 1 — pagination envelope.** `StandardCursorPagination`
  returns DRF's native `{results, next, previous}`, not the handoff's
  `{data, next_cursor}`. Recommendation: **change the client to consume DRF's
  native shape** rather than add a serialization-layer wrapper — it's one
  fewer moving part, and `next`/`previous` are full URLs the client can follow
  directly (simpler than reconstructing `?cursor=`). Flagged as Decision #4.
- **Adjustment 2 — idempotency and StoredFile are foundation gaps, not
  client-stack gaps.** Build the `Idempotency-Key` mixin before either exam
  autosave or the offline attendance queue (Milestone 0). `StoredFile` is out
  of scope for these six items and is not built here.

## 3. Per-item API surface

Contract for everything new below: every write accepts `Idempotency-Key`
(once Milestone 0 lands); every list is `StandardCursorPagination`
(`?cursor=&limit=`, native DRF envelope, per Decision #4).

### Gradebook grid + merge prompt (ACD-006, TCH-007, TCH-005/006/014)

- Reuse `GET .../gradebook/` and `PUT .../assessments/:id/scores/` as-is.
- **Confirmed gap**: `ScoreConflictError` ([apps/academic/services.py:55](../apps/academic/services.py))
  carries only `current_score/current_version/current_feedback/current_descriptor`
  — no author or timestamp, even though `AssessmentScore` already has
  `graded_by`/`graded_at` fields. The design requires "yours and theirs, with
  author and timestamp." Fix: add `current_graded_by`/`current_graded_at` to
  `ScoreConflictError.__init__` and the `409` response body in
  `AssessmentViewSet.scores` ([apps/academic/views.py:274-284](../apps/academic/views.py)).
  Small, targeted service+view change, not a new endpoint.
- Grading queue: `GET .../exams/:id/grading-queue/` exists for exam essays;
  homework's queue is assembled client-side from `GET .../homework/:id/submissions/`
  filtered/sorted by status — confirm this is sufficient or add a
  dedicated `GET .../homework/grading-queue/?class_subject_id=` if
  cross-homework aggregation is needed (the design's queue spans multiple
  homework assignments, not one). **Likely a small new read endpoint —
  scope during Milestone 2, not assumed here.**

### Homework completion bar (ACD-030)

- Reuse `GET .../homework/:id/submissions/`, `POST .../remind/`.
- **Confirmed gap**: `get_homework_completion` ([apps/academic/services.py:732](../apps/academic/services.py))
  returns only `{total, submitted, not_started}` — two states. The design's
  four-segment bar (graded/submitted/late/missing) needs graded count (join
  `AssessmentScore`-equivalent grading state on `HomeworkSubmission`) and a
  late-vs-on-time split (submission `submitted_at` vs `homework.due_at`) plus
  the already-overdue-and-still-missing split. Rework the function to return
  all four buckets; this is real, scoped Milestone-1 work, not a maybe.
- **Confirmed gap**: no `remind` delivery-receipt read path exists — `remind_unsubmitted`
  ([apps/academic/services.py:749](../apps/academic/services.py)) creates
  notification intents but nothing in `apps/academic` or `apps/notifications/urls.py`
  exposes per-recipient `NotificationDelivery` status (`SENT`/`DELIVERED`/`FAILED`)
  filtered by a triggering remind call. Add
  `GET /api/v1/academic/homework/:id/remind-status/` returning each
  `NotificationDelivery` row's guardian + status for the most recent remind.

### Teacher mobile agenda (ACD-019)

- Reuse `GET /teacher/agenda`, `POST .../period-attendance/`,
  `POST /period-attendance/sync/`.
- **Add**: substitution accept/decline. No endpoint exists today. New:
  `POST /api/v1/timetable/substitutions/:id/accept/` and
  `POST /api/v1/timetable/substitutions/:id/decline/` (body: `{reason}`),
  the latter notifying the assigning admin (reuse
  `apps.notifications` intent creation, same pattern as `assign_substitution`'s
  existing `SUBSTITUTE_ASSIGNED` notify call).
- **Confirmed gap**: `apps/notifications/providers.py` has only
  `MockPushProvider` ([apps/notifications/providers.py:120](../apps/notifications/providers.py))
  — no real FCM/APNs integration, and no device-token model or registration
  endpoint anywhere in `apps/notifications`. Add: push-token registration,
  `POST /api/v1/me/push-tokens/` (device token + platform), plus a real push
  provider before launch (mock is fine through development). Foundation-layer,
  one model + one endpoint + one provider swap.
- **Confirmed safe**: `sync_offline_period_attendance_batch`
  ([apps/attendance/services.py:1125](../apps/attendance/services.py)) is
  idempotent by construction — `submit_period_attendance` is an
  `update_or_create` keyed by student+slot+date, and each entry fails
  independently without discarding the rest of the batch. The offline
  queue's client-side idempotency key is defense-in-depth on the network
  layer only, not required for write correctness.

### Exam lockdown client (ACD-024)

- Reuse `.../answers/`, `.../submit/`, `.../focus-loss/`.
- These three are the ones that most need Milestone 0's `Idempotency-Key`
  mixin: autosave POSTs every ≤20s, keyed by attempt+question, must be safe
  to retry over a flaky connection without double-counting or corrupting
  `remaining_seconds` math.
- Proctor console (6-column live grid) needs a **new** endpoint:
  `GET /api/v1/academic/exams/:id/proctor/` → per-student rows
  (progress, focus-loss count, connection/last-save recency, action). Poll-based
  (no WebSockets, per platform constraint) — client polls every 5-10s.
  This is new server work, not just a client screen.

## 4. `docs/frontend-plan.md` — milestones

### Milestone 0 — Shared foundation (blocks everything else)

1. `frontend/` tree: `templates/base.html`, `templates/components/`,
   Tailwind config as the design-token source of truth (colors, type scale,
   spacing steps, square-corner rule — transcribe directly from the handoff
   README's token tables), `frontend/static/` build pipeline (Tailwind CLI,
   checked-in output or a documented build step — no runtime Node
   dependency).
2. Idempotency mixin: `apps/core/idempotency.py`, a DRF `APIView`/`ViewSet`
   mixin backed by the existing `IdempotencyRecord` model — hash
   `(request.method, request.path, body)`, replay stored response on a key
   match with a differing body as `409`, on a key+body match as the original
   `2xx`.
3. Component primitives (server-rendered, HTMX-driven): status badge
   (dot + label, semantic palette only, brand red never used for status),
   loading/stale/offline banner (mandatory on every screen per the handoff),
   cursor-paginated list partial (HTMX `hx-get` "load more").
4. Indonesian-first i18n: confirm `USE_I18N`/`LOCALE_PATHS` in
   `educore/settings/base.py` (Indonesian source strings already appear
   throughout `apps/*/views.py` error messages — the pattern is established,
   just needs a `django.po`/`en` translation layer added for the 30%
   text-expansion requirement to be testable).
5. Icon set decision (Decision #3 below) — the design's glyphs are
   explicit placeholders.

### Milestone 1 — Homework completion bar (ACD-030)

Smallest, most self-contained item; ships first to validate the Milestone 0
foundation end-to-end before the harder grid work. Web + mobile-card variant
from the same template partial (Tailwind responsive, not two implementations).

### Milestone 2 — Gradebook grid, keyboard entry, merge prompt (ACD-006, TCH-007, TCH-005/006/014)

The largest item. Sequence: read-only grid render → keyboard nav (arrows/Tab/Enter/Esc)
→ dirty/saving/saved cell states wired to the bulk `scores` endpoint →
conflict state + merge-prompt modal (design gap — build from the component
library per the handoff's own note) → grading queue view, with the
possible small new endpoint scoped in §3 above.

### Milestone 3 — Homework grading queue integration (TCH-014 tail end)

Folds into Milestone 2's queue work; called out separately only because it's
its own req ID and may ship as its own PR.

### Milestone 4 — Teacher mobile agenda (ACD-019)

Mobile track (see below) milestone 2. Depends on Milestone 0's mobile
project setup and the two new backend endpoints (substitution
accept/decline, push-token registration).

### Milestone 5 — Exam lockdown client (ACD-024)

Depends on Milestone 0's idempotency mixin. Includes the new proctor-console
endpoint. Student-facing lockdown screen is web (Django templates + HTMX
polling for the countdown resync), proctor console is web (teacher/admin).

### Mobile track (React Native) — parallel to Milestones 1-3

0. Project setup: `mobile/` RN project (Expo vs. bare — **Decision #2**),
   JWT auth flow against `DualAuthBackend` (phone/email + password →
   `rest_framework_simplejwt` token pair), token refresh, secure token
   storage.
1. Offline cache + queue: local store (SQLite via a RN persistence library —
   **Decision #2** covers this too) for the agenda's offline-first
   attendance queue — idempotency-keyed entries, oldest-first replay, pending
   count badge, wired to `POST /period-attendance/sync/`.
2. Agenda surface (ACD-019): the three screens per the handoff — agenda list
   scrolled to current period, per-period roll call (gate-prefilled `ALPA`
   with badge), substitution notification with accept/decline.
3. Push notifications: register device token against the new
   `POST /me/push-tokens/`, handle `SUBSTITUTE_ASSIGNED` push → in-app
   notification with the flags-only medical-note summary.

## 5. Decisions needed

1. **Pagination envelope** — accept DRF's native `{results, next, previous}`
   cursor shape (recommended, matches what already ships) instead of the
   handoff's `{data, next_cursor}`, or add a thin serializer wrapper across
   every list endpoint these six items touch? Assumed: adopt native shape.
2. **React Native tooling** — Expo (faster setup, OTA updates, some native
   module limits) or bare RN CLI (full native access, more setup)? Neither
   is in the repo yet; this is a fresh choice with no existing precedent to
   defer to. Not assumed — needs your call before Milestone 0's mobile setup.
3. **Icon set** — the design's glyphs (`◷ ▤ ◈ ☰`) are explicit placeholders.
   No icon library exists in the repo. Proposal: Heroicons (outline set,
   square-corner-compatible, MIT-licensed, works as inline SVG with no build
   dependency) — confirm or name an alternative.
4. **Merge-prompt modal layout** — the design file specifies the conflict
   cell state and the no-silent-merge rule but explicitly not the modal's
   final layout (its own gap note). Plan assumes: build it from the
   `EduCore Design System.dc.html` component library (modal/dialog
   primitives already specified there) rather than freehand — confirm that's
   the right source, or provide a reference.
5. **Homework grading-queue aggregation** — confirm whether the existing
   per-homework `submissions` list is sufficient for TCH-014's cross-homework
   queue, or whether a new aggregating endpoint is required (flagged in §3,
   scope deferred to Milestone 2 rather than guessed here).
6. **`StoredFile` / signed-GCS-upload gap** — confirmed out of scope for all
   six items, but logged as blocking any future attachment-upload UI
   (including the existing `HomeworkViewSet.upload_file`, which currently
   violates the "never write to server disk" rule). Confirm this should be
   filed as a separate Notion Open Item rather than folded into this plan.

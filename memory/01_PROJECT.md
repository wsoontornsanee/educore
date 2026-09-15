# 01_PROJECT — Current Status & Living State

## 1. Project Overview
- **Name:** EduCore (Sistem Operasi Sekolah Swasta)
- **Target Institutions:** Indonesian private schools (sekolah swasta, madrasah, pesantren modern, SPK/National-Plus).
- **Core Stack:** Python 3.12, Django 5.x LTS monolith, MySQL 8.0, cron background scheduling, HTMX + Tailwind for web, React Native (Expo) for mobile.
- **Design System:** `spec/17-design-system-and-ui.md` (Plus Jakarta Sans + IBM Plex Sans/Mono, Merah flag-saturated `#C8102E`, institutional square 0px radii, 5 mandatory screen states).

---

## 2. Current Build Status
- **Current Step:** Milestone M6 (Step 6: Academic Module, P1 Classroom) Done. M7–M9 (teacher suite UX, remaining P1 scope) not yet started.
- **Target Milestone:** P0 Pilot Core [DONE] (M0–M5) -> P1 Classroom (M6–M9): academic module [DONE], teacher suite, report cards [DONE], homework [DONE] (`spec/00 §6`).
  - *M0 (Step 0) [DONE]:* Django project skeleton, `core` app (`TenantModel`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `drain_tasks`, advisory locks, crontab).
  - *M1 (Step 1) [DONE]:* Tenancy, auth, RBAC, feature entitlements, foundation portal, seed data (`identity`, `foundation`). Demo: Foundation admin logs in, sees two schools.
  - *M2 (Step 2) [DONE]:* Student/staff records + bulk XLSX import (`identity`).
  - *M3 (Step 3) [DONE]:* Credentials, gate attendance events, edge agent stub (`attendance`, `hardware`).
  - *M4 (Step 4) [DONE]:* Notifications service + WhatsApp arrival messaging (`notifications`).
  - *M5 (Step 5) [DONE]:* Finance, SPP billing schedules, invoice generation, virtual accounts & double-entry ledger (`finance`).
  - *M6 (Step 6) [DONE]:* Academic module — subjects, class groups/enrollment, gradebook, weighted grading, timetable with conflict detection & substitutions, homework, online exams with auto-grading, report cards with approval workflow & arrears gate (`academic`). P1 Classroom milestone complete for the backend scope defined here.


---

## 3. Retrospectives & Key Architectural Discoveries

1. **Fail-Closed Tenancy:** `TenantManager.get_queryset()` was designed to return `.none()` if no active thread-local `foundation_id` is set. This guarantees zero cross-tenant data leaks if an endpoint or command forgets to scope context.
2. **Float Rejection in MoneyField:** Validating at both `to_python` and `get_prep_value` prevents any accidental Python `float` assignment, ensuring all financial calculations retain strict decimal precision (`CUR-005`).
3. **Database Driver Agnosticism for Dev/CI:** Integrated `pymysql.install_as_MySQLdb()` fallback in `educore/__init__.py` allowing seamless development on platforms without precompiled C-wheels for `mysqlclient`, alongside `docker-compose.yml` for local MySQL 8.
4. **Advisory Lock Safety:** Named MySQL locks (`GET_LOCK`) cleanly prevent overlapping cron job executions across horizontal app nodes. Fallback in-memory mutex allows offline tests to verify locking semantics without external services.
5. **Worker Task Draining:** `drain_tasks` uses `SELECT ... FOR UPDATE SKIP LOCKED` where supported (MySQL 8) and safely executes tasks under the task's originating `foundation_id` context.
6. **Swappable User Model in Initial Migration:** Django requires custom `AUTH_USER_MODEL` to be declared in the app's initial migration (`0001_initial.py`) because `admin.0001_initial` depends on `('identity', '__first__')`. Attempting to introduce `User` in `0002_...` triggers a lazy reference `ValueError` in `admin.LogEntry.user`.
7. **UU PDP Person PII Vault Isolation:** Identity-bearing PII (`nik`, `dob`, `gender`, `address`) is stored exclusively in `persons` (`Person` model), while `users` (`User` model) holds only auth credentials, phone/email identifiers, and account lockout security fields.
8. **RBAC Scope Isolation & Fail-Closed Guardrails:** Roles are assigned at either `FOUNDATION` scope or `SCHOOL` scope. `HasRequiredPermission` strictly fails closed if a view forgets to define `required_permission` (`IAM-010`), and school-scoped roles cannot access sibling schools (`IAM-012`).
9. **Layer 3 Tenancy & Standard CursorPagination:** Layer 3 multi-tenancy derivation from authenticated user in `TenancyMiddleware` + `TenantManager` completely seals cross-tenant visibility (returning 404 for sibling foundation entities). `StandardCursorPagination` enforces `-created_at` ordering across all list endpoints.
10. **Granular Feature Entitlements Hierarchy:** `foundation_entitlements` evaluates per-school overrides before foundation-wide defaults (`IAM-023`). Disabled modules raise HTTP 403 `MODULE_NOT_ENTITLED` (`IAM-024`) via `RequiresModuleEntitlement`, and `GET /api/v1/me` exposes active module flags to hide navigation surfaces dynamically.
11. **Atomic Bulk Import with Exhaustive Diagnostics:** `StudentBulkImporter` validates entire batches across data types, phone numbers, and DB/in-file uniqueness before committing (`spec/02 §8.3`), offering dry-run preview mode (`?dry_run=true`) and returning specific row error lists upon rejection.
12. **Idempotent Gate Ingestion & Debouncing:** Ingestion accepts batches indexed by client `event_uuid` (`HW-005`), filtering rapid repeated scans within `debounce_seconds` (`ATT-007`) and inferring in/out direction automatically for bidirectional turnstiles (`ATT-008`).
13. **Live Gate Console & Edge Synchronization:** Real-time gate monitoring uses cursor-based polling (`ARC-015`, `ATT-013`) by monotonically increasing ID without heavy websocket infra; offline edge devices sync credential whitelists and rules incrementally via timestamp-filtered deltas (`spec/12 §3, §7`).
14. **Multi-Channel Notification Ladder & WhatsApp Gate Alerts:** Inbound gate scans trigger parent arrival WhatsApp messages in < 5s (`ATT-006`). Deduplication prevents re-notifying on debounce (`NTF-003`, `ATT-007`), quiet hours defer non-critical messages (`NTF-002`), emergency alerts bypass restrictions (`NTF-013`), and provider outages automatically cascade across fallback channels (`WHATSAPP` -> `PUSH` -> `SMS` -> `EMAIL`, `NTF-006`).
15. **Gapless Invoice Numbering & Idempotent Generation:** Invoices use sequential per-school per-year counters (`InvoiceNumberSequence`) allocated via `SELECT ... FOR UPDATE` (`FIN-004`). Monthly generation runs are strictly idempotent per `(student, period)`, exclude non-active students (`FIN-005`), calculate explicit IDR `PEMBULATAN` line items (`FIN-008c`), support dry-run previewing (`FIN-008`), and are locked by advisory locks (`ARC-007`).
16. **Payment Webhook Tenancy & Balanced Double-Entry Ledger:** Public webhooks operate without session auth, requiring explicit tenant resolution via `.all_tenants` and `with tenant_context(foundation_id):` to prevent fail-closed query rejection. Payments strictly allocate oldest-first, record partial/full status, hold overpayment as reusable `StudentCreditBalance` (`FIN-015`), and post balanced double-entry `ledger_entries` (`sum(debit) == sum(credit)` per journal per currency to 0.00) using the standard Chart of Accounts (`FIN-021`, `FIN-022`, `CUR-020`).
17. **Academic Core: Weighted Grading & RBAC Reuse:** `AcademicYear`/`Term`/`ClassEnrollment` were net-new (spec/04 referenced `academic_year_id`/`term_id` without defining owning entities); scoped under `apps.academic` rather than `identity` since they're academic-specific. Final term grade (`ACD-008`) is a weighted average of individual published assessments (not category-level) restricted to `SUMMATIVE`/`EXAM`/`PROJECT` types, whose weights must sum to exactly 100% at computation time or raise `WEIGHT_CONFIG_INCOMPLETE`; a counted assessment missing a student's score returns `INCOMPLETE` rather than treating it as zero (`ACD-009`). Reused existing `grades.read`/`grades.write` RBAC permission keys (already present in `ROLE_PERMISSIONS`) instead of minting new ones. Grade-change audit trail (`ACD-005`) relies on `AuditEvent.diff` for before/after retrieval rather than a separate revision table, consistent with finance's correction pattern.
18. **Timetable Conflict Detection Scope:** `TimetableSlot` is keyed off `class_subject` (which already carries `class_group` + `teacher`) rather than duplicating both FKs as spec/04's entity list literally shows, avoiding a drift-prone redundant field. `ACD-017` conflict checks (class/teacher/room double-booking) run at slot-creation time scoped to `(day_of_week, period_no)`; no separate `PeriodGrid`/break-slot config entity was introduced (`ACD-018`) — period grid is currently just a free `period_no` integer per school, deferred until a real per-school grid config is needed. Substitute notification delivery (`ACD-019` "notify the substitute") is deferred: wiring a new `NotificationCategory` + approved template into the existing `notifications` app was judged out of scope for this slice; substitutions persist and are audit-logged only.
19. **Online Exams: Server-Side Timing & Deferred Client Enforcement:** `ExamAnswer` is net-new (not in spec/04's entity list) — required to persist per-question answer state for resume-after-disconnect (`ACD-021`); one row per `(attempt, question)`, upserted on every autosave. `compute_remaining_seconds` derives strictly from `started_at + duration_min`, clamped to `window_end` — client-submitted timestamps are never trusted (`ACD-022`). Auto-grading (`ACD-023`) dispatches by `ExamQuestion.type`: MCQ/TRUE_FALSE exact-match, SHORT case/whitespace-normalized match against an accepted-answers list, MATCHING exact-pairs match, MULTI supports optional partial credit (`(correct_hits - wrong_hits)/len(correct)`, floored at 0); ESSAY always routes to `grading-queue` for manual scoring, and `final_score` stays `None` until every ESSAY answer in the attempt is graded. `shuffle`/`one_question_at_a_time`/`block_back_navigation`/`full_screen_lock` (`ACD-024`) are stored in `Exam.settings` and the per-attempt question order is shuffled server-side and returned in `question_order`, but client-side enforcement (lockdown UI, back-navigation blocking) is out of scope for this backend-only repo — no frontend exists yet. `focus_loss_count` is an unbounded counter, never auto-punitive. Late submissions (`ACD-025`) are handled by `auto_submit_if_expired`, called on every read/write path against an attempt, which finalizes with whatever answers were persisted (no answers lost). 300-concurrent-student load testing (`ACD-026`) has no supporting infra in this repo and was not attempted.
20. **Report Cards: PDF Fallback & Arrears Gate:** `render_report_card_pdf` lazily imports `weasyprint` and falls back to writing plain HTML (`.html` `pdf_key` instead of `.pdf`) when weasyprint's native libraries (pango/cairo/gobject) aren't installed — true in this dev/CI environment despite `weasyprint` being a listed dependency, since those are system libraries pip cannot install. The rendered layout itself is a minimal functional template, not the branded design — spec/appendix's open decision #6 ("Report Card Template: exact layout and signed sample needed") is still unresolved. `ReportCard` uses `(student, term)` uniqueness scoped by `is_current`; `revise_report_card` only fires on a `PUBLISHED` card, flips `is_current=False` on the old row (kept immutable, status stays `PUBLISHED`) and creates a new `version+1` `DRAFT` row — satisfying `ACD-016`'s "corrections create a new version with a visible revision number" without ever mutating a published PDF. `ReportCardPolicy.block_rapor_on_arrears` (default off) reuses `Invoice.is_overdue` from `apps.finance` rather than re-deriving overdue logic; `get_visible_report_card` is the single gate both a future parent-app endpoint and `StudentReportCardView` call — parents never see anything but `PUBLISHED` + non-arrears-blocked cards (`ACD-013`/`ACD-014`). Guardian/parent-authorization filtering (verifying the caller is actually that student's parent) is deferred — out of scope for this backend-only repo, consistent with the parent mobile app being a separate, not-yet-built surface.
21. **Homework: File Metadata Only, No Upload Pipeline:** Consistent with `Student.photo_key`/`Person` PII vault's existing pattern of storing filesystem-path strings rather than binary blobs, `HomeworkSubmission.files` is a `JSONField` list of `{key, filename, size, content_type}` metadata dicts — no multipart upload endpoint exists yet anywhere in the repo, so actual file storage is deferred until one is built. `ACD-027` limits (≤5 files, ≤20MB each, pdf/jpg/png/docx only) are validated against this metadata in `validate_submission_files`. A resubmission is `update_or_create`d onto the same row (one `HomeworkSubmission` per `(homework, student)`) rather than versioned. `remind_unsubmitted` (`ACD-030`) enforces the 12h rate limit via `Homework.last_reminded_at` and computes the unsubmitted roster from `ClassEnrollment`, but — like TASK-024's substitution notification — does not actually send anything. Note: `NotificationCategory.HOMEWORK` already exists in `apps.notifications` (unused until now), so wiring `ACD-029`/`ACD-030` delivery mainly needs a seeded, approved `NotificationTemplate` row for a homework-assigned/reminder key, not a new category — smaller lift than TASK-024's substitute-notification gap; still deferred here to keep this slice scoped to the academic domain.

---

## 4. Active Tasks & Immediate Next Actions (Step 6 / Milestone M6)

| Task ID | Component | Description | Status |
|---|---|---|---|
| `TASK-001` | Core Setup | Step 0 Skeleton & Foundation primitives | Completed |
| `TASK-008A`| Identity Core | Implement `Foundation` and `School` models + `seed_demo_foundation` | Completed |
| `TASK-008B`| Identity Auth | Implement `User` model, `Person` (PII vault), and authentication services | Completed |
| `TASK-010` | RBAC & Auth | Enforce role permissions matrix, session auth for web, JWT for mobile (`spec/02 §3, §4`) | Completed |
| `TASK-009` | Foundation App | Implement `apps/foundation/` portal models and school management views (`spec/03`) | Completed |
| `TASK-011` | Entitlements | Implement `foundation_entitlements` gating (`spec/02 §6`) and `GET /me` | Completed |
| `TASK-012` | Student Records | Implement `Student`, `Guardian`, and `GuardianLink` models (`spec/02 §2`) | Completed |
| `TASK-013` | Staff Records | Implement `Staff` model and offboarding lifecycle (`spec/02 §2, §5`) | Completed |
| `TASK-014` | Bulk Import | Implement atomic student XLSX import with dry-run diff preview (`spec/02 §5`) | Completed |
| `TASK-015` | Credentials | Implement RFID card, QR credential management, and revocation (`spec/05 §2`, `spec/12 §5`) | Completed |
| `TASK-016` | Gate Events | Implement gate event batch ingestion, debouncing, and daily attendance derivation (`spec/05 §2, §3, §4`) | Completed |
| `TASK-017` | Gate Console | Implement live gate console polling, manual check-in, and edge sync stub (`spec/05 §4`, `spec/12 §3, §7`) | Completed |
| `TASK-018` | Notifications | Notification templates, recipient resolution, dispatch queue, provider stub (`spec/13`) | Completed |
| `TASK-019` | WhatsApp Arrival | Gate scan WhatsApp arrival notification, quiet hours, status webhook (`spec/05 §4 ATT-006`, `spec/13 §4, §6`) | Completed |
| `TASK-020` | Fee Structures | Implement fee items, schedules (SPP, Uang Pangkal), discount policies, and rounding (`spec/06 §2`, `spec/16`) | Completed |
| `TASK-021` | Invoice Generation | Monthly batch invoice generation, rounding line item (`PEMBULATAN`), and notifications (`spec/06 §3`) | Completed |
| `TASK-022` | VA & Payments | Virtual Account allocation, payment webhook processing, double-entry ledger journals (`spec/06 §4, §5`, `spec/16`) | Completed |
| `TASK-023` | Academic Core | Subjects, class groups/enrollment, class-subjects, learning objectives, assessments, gradebook, weighted final grade (`spec/04 §2, §3, §8`, `ACD-001` to `ACD-009`) | Completed |
| `TASK-024` | Timetable | Timetable builder with conflict detection, substitutions (`spec/04 §5`, `ACD-017` to `ACD-020`) | Completed |
| `TASK-025` | Homework | Homework assignment/submission, completion tracking, reminders (`spec/04 §7`, `ACD-027` to `ACD-030`) | Completed |
| `TASK-026` | Online Exams | Exam attempts, server-side timing, auto-grading (`spec/04 §6`, `ACD-021` to `ACD-026`) | Completed |
| `TASK-027` | Report Cards | Rapor generation, approval workflow, PDF, arrears gate (`spec/04 §4`, `ACD-010` to `ACD-016`) | Completed |

---

## 5. Open Technical & Product Decisions

From `spec/appendix.md §3`:

1. **Virtual Account (VA) Provider:** Does the pilot foundation use a single bank, and does it support stable per-student VA numbers?
2. **Convenience Fee Allocation:** Is payment convenience fee absorbed by the school or passed to parents (per-school or foundation-wide)?
3. **Report Card Withholding Policy (`ACD-014`):** Is `block_rapor_on_arrears` culturally and contractually acceptable to the pilot foundation?
4. **Canteen Float Legal Entity (`CMP-027`):** Who legally holds the canteen wallet float (foundation, cooperative, or vendor entity)?
5. **Curriculum Scope:** Which curriculum variants are in scope at launch — Kurikulum Merdeka only, or also madrasah-specific subjects (Kemenag)?
6. **Report Card Template:** Exact layout and signed sample needed before building `04 §4`.
7. **Biometric vs Card Authentication:** Is facial recognition in scope for gate attendance at the pilot, or card-only? (Impacts consent and edge processing).
8. **Multi-Currency Requirements:** Which non-IDR currencies are actually needed at launch by pilot schools (affects FX reporting priority)?
9. **Cron Host Topography (`ARC-013`):** Does the pilot deployment have a dedicated cron host or a single VM?

---

## 6. Blockers & Risks
- None currently active.

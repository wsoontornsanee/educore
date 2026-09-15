# 01_PROJECT — Current Status & Living State

## 1. Project Overview
- **Name:** EduCore (Sistem Operasi Sekolah Swasta)
- **Target Institutions:** Indonesian private schools (sekolah swasta, madrasah, pesantren modern, SPK/National-Plus).
- **Core Stack:** Python 3.12, Django 5.x LTS monolith, MySQL 8.0, cron background scheduling, HTMX + Tailwind for web, React Native (Expo) for mobile.
- **Design System:** `spec/17-design-system-and-ui.md` (Plus Jakarta Sans + IBM Plex Sans/Mono, Merah flag-saturated `#C8102E`, institutional square 0px radii, 5 mandatory screen states).

---

## 2. Current Build Status
- **Current Step:** Milestone M3 (Step 3: Credentials, Gate Attendance Events & Edge Agent Stub) In Progress.
- **Target Milestone:** Pilot Core (P0: Milestones M0–M4).
  - *M0 (Step 0) [DONE]:* Django project skeleton, `core` app (`TenantModel`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `drain_tasks`, advisory locks, crontab).
  - *M1 (Step 1) [DONE]:* Tenancy, auth, RBAC, feature entitlements, foundation portal, seed data (`identity`, `foundation`). Demo: Foundation admin logs in, sees two schools.
  - *M2 (Step 2) [DONE]:* Student/staff records + bulk XLSX import (`identity`).
  - *M3 (Step 3) [IN PROGRESS]:* Credentials, gate attendance events, edge agent stub (`attendance`, `hardware`).
  - *M4 (Step 4):* Notifications service + WhatsApp arrival messaging (`notifications`).


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

---

## 4. Active Tasks & Immediate Next Actions (Step 3 / Milestone M3)

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
| `TASK-016` | Gate Events | Implement gate event batch ingestion, debouncing, and daily attendance derivation (`spec/05 §2, §3, §4`) | Completed (Pending PR Merge) |
| `TASK-017` | Gate Console | Implement live gate console polling, manual check-in, and edge sync stub (`spec/05 §4`, `spec/12 §3, §7`) | Pending |




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

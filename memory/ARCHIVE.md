# ARCHIVE — Historical Milestones & Decision Log

## Initial Setup & Specifications
- **Date:** 2026-09-14
- **Milestone:** Project Kickoff & Specification Import
- **Details:**
  - Repository initialized at `https://github.com/wsoontornsanee/educore`.
  - Specs `00-overview.md` through `16-currency-and-money.md`, `index.md`, and `appendix.md` imported and committed to `main`.
  - Architectural agreement ratified: single Django monolith, MySQL 8 only, cron-driven background tasks, 3-layer tenancy isolation, and strict `DECIMAL(18,2)` money handling.

## Step 0: Foundation & Skeleton Setup
- **Date:** 2026-09-14
- **Milestone:** Pilot Core M0 (Step 0) Completed & Merged (PR #1)
- **Details:**
  - Agent memory architecture established (`memory/00_CORE.md`, `memory/01_PROJECT.md`, `AGENTS.md`, `CLAUDE.md`, `SOUL.md`, `USER.md`).
  - Django monolith layout created with pure-Python dependencies (`educore/settings/{base,local,staging,production}.py`, `educore/middleware/{tenancy,audit,timezone}.py`).
  - Base models implemented in `apps/core/`: `TenantModel` & `TenantManager` with thread-local scoping, `MoneyField` with strict `DECIMAL(18,2)` & float rejection, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `IdempotencyRecord`.
  - Implemented MySQL named advisory lock utilities (`acquire_advisory_lock`, `release_advisory_lock`, `advisory_lock`).
  - Implemented `drain_tasks` management command with `SELECT ... FOR UPDATE SKIP LOCKED` and exponential backoff retry handling.
  - Implemented `sample_cron_job` management command with advisory locking and `JobRun` logging.
  - Deployed canonical `deploy/crontab` schedule.
  - 15 automated test cases passing across tenancy isolation, money validation, task queue draining, and advisory locking.
  - PR #1 merged into `main`.

## Specification Update: Spec 17 Design System & UI Specification
- **Date:** 2026-09-14
- **Milestone:** Design System & UI Specification Integrated (PR #2)
- **Details:**
  - Formulated `spec/17-design-system-and-ui.md` based on official design artifact.
  - Defined 4 core principles: Red is for action, Numbers are the interface, Built for 3G phone, Bahasa Indonesia first.
  - Standardized Merah brand palette (`#C8102E`), Warm Ink & Paper neutrals, semantic tokens, and 6 attendance tokens (`HADIR`, `TERLAMBAT`, `SAKIT`, `IZIN`, `ALPA`, `DISPEN`).
  - Defined typography scale with Plus Jakarta Sans, IBM Plex Sans, and tabular IBM Plex Mono.
  - Integrated full surface mockups: Foundation Dashboard (1440px), Live Gate Console (3s polling), Teacher Gradebook (keyboard-first), Parent Mobile App, and Canteen POS (tablet).
  - Merged into `main` and synced with `spec/EduCore-Design-System.pdf`.

## Step 1.1: Foundation & School Core Entities + Demo Seeder
- **Date:** 2026-09-14
- **Milestone:** Step 1.1 Completed & Merged (PR #3)
- **Details:**
  - Implemented `Foundation` model as the top-level multi-tenancy anchor (NPWP, address, reporting currency IDR, plan tier, status).
  - Implemented `School` model inheriting `core.models.TenantModel` (NPSN, educational level, curriculum, base currency IDR, composite indexes).
  - Implemented `seed_demo_foundation` management command, creating `Yayasan Al-Hikmah Nusantara` with SD, SMP, and SMA campuses.
  - 4 automated tests implemented in `apps/identity/tests/test_foundation_school.py`, asserting multi-tenant query isolation, soft deletion, and command idempotency (total 19 tests passing).
  - PR #3 merged into `main`.

## Step 1.2: Custom User Model, Persons PII Vault, and Phone/Email OTP Authentication
- **Date:** 2026-09-14
- **Milestone:** Step 1.2 Completed & Merged (PR #4)
- **Details:**
  - Implemented custom `User` model (`apps/identity/models.py`) supporting dual phone E.164 (`+62...`) and email authentication, account lockout protection (`IAM-008`: 10 failed attempts locks account for 15 minutes), and TOTP MFA secrets.
  - Implemented `Person` PII vault model complying with UU PDP No. 27/2022 (`nik`, `full_name`, `dob`, `gender`, `address`).
  - Implemented `OTPChallenge` model and services (`apps/identity/services.py`) with 5-minute TTL, 6-digit cryptographic generation, rate limiting (max 3 per 15 min), and 5-attempt brute-force protection.
  - Implemented `DualAuthBackend` (`apps/identity/backends.py`) supporting phone/email login with password or OTP.
  - Updated `seed_demo_foundation` to seed demo Yayasan superadmin user (`+6281234567890`, `admin@alhikmah.sch.id`).
  - Consolidated initial identity migrations into a clean `0001_initial.py` to satisfy Django's swappable user model migration constraints.
  - 6 new automated tests in `apps/identity/tests/test_users_and_auth.py` (total 25 tests passing).
  - PR #4 merged into `main`.

## Step 1.3: Role Assignment Model, RBAC Matrix, and Permission Enforcement
- **Date:** 2026-09-14
- **Milestone:** Step 1.3 Completed & Merged (PR #5)
- **Details:**
  - Implemented `RoleAssignment` model (`apps/identity/models.py`) with `TenantModel` inheritance, `FOUNDATION` and `SCHOOL` scoping, unique constraints, and composite indexes.
  - Implemented RBAC matrix and evaluator engine (`apps/identity/rbac.py`) mapping 6 canonical roles (`foundation_admin`, `school_admin`, `finance_officer`, `teacher`, `counsellor`, `parent`) across 11 functional domains (`spec/02 §4.2`).
  - Enforced scope isolation preventing school-scoped roles from accessing sibling schools (`IAM-012`).
  - Implemented DRF permission classes (`apps/identity/permissions.py`): `HasRequiredPermission` with fail-closed enforcement on missing `view.required_permission` (`IAM-010`), and `IsFoundationAdmin`.
  - Updated `seed_demo_foundation` to assign `foundation_admin` at `FOUNDATION` scope to the demo admin user idempotently.
  - 7 new automated tests in `apps/identity/tests/test_rbac.py` (total 32 tests passing).
  - PR #5 merged into `main`.

## Step 1.4: Foundation Portal Views, School Management API, and Dashboard KPIs
- **Date:** 2026-09-14
- **Milestone:** Step 1.4 Completed & Merged (PR #6) - Milestone M1 Acceptance Achieved
- **Details:**
  - Achieved Milestone M1 acceptance goal: Foundation admin authenticates and views schools under their Yayasan via `/api/v1/schools/`.
  - Implemented `RptFoundationKPI` model (`rpt_foundation_kpis`) with `MoneyField` (`DECIMAL(18,2)`) for high-performance dashboard KPI rollups (`spec/03 §4`, `FND-005`).
  - Implemented `SchoolViewSet` (`/api/v1/schools/`) with 3-layer tenancy scoping, RBAC action permissions (`school_config.read` / `school_config.write`), soft deletion enforcement (`deleted_at`), and immutable `AuditEvent` logging.
  - Implemented `FoundationSettingsView` (`/api/v1/foundation/settings`) and `FoundationKPIView` (`/api/v1/foundation/kpis`).
  - Implemented `StandardCursorPagination` in `apps/core/pagination.py` enforcing `-created_at` ordering across all cursor-paginated endpoints.
  - Updated `TenancyMiddleware` to support `_force_auth_user` for API client tests.
  - 9 new automated tests across `apps/foundation/tests/` (total 41 tests passing).
  - PR #6 merged into `main`.

## Step 1.5: Feature Entitlements Gating & User Profile API
- **Date:** 2026-09-14
- **Milestone:** Step 1.5 Completed & Merged (PR #7) - Milestone M1 (Pilot Core) 100% Complete
- **Details:**
  - Implemented `FoundationEntitlement` model (`foundation_entitlements` table) with `TenantModel` inheritance, school-level overrides (`school_id`), module choices (`academic`, `attendance`, `finance`, `wallet`, `campus_life`, `payroll`, `analytics`, `hardware`), and JSON limits (`IAM-023`, `IAM-025`).
  - Implemented `apps/identity/entitlements.py` engine resolving entitlements hierarchically: School override -> Foundation default -> Enabled by default (`IAM-024`).
  - Implemented DRF permission `RequiresModuleEntitlement` and custom exception `ModuleNotEntitled` (HTTP 403 `MODULE_NOT_ENTITLED`).
  - Implemented `GET /api/v1/me` (`CurrentUserView`) returning authenticated user profile, roles, assigned schools, and active module entitlement flags.
  - Implemented `/api/v1/foundation/entitlements/` (`FoundationEntitlementViewSet`) with RBAC gating (`school_config.read` / `school_config.write`).
  - Updated `seed_demo_foundation` to seed all 8 canonical modules for the demo foundation idempotently.
  - 5 new automated tests in `apps/identity/tests/test_entitlements.py` (total 46 tests passing).
  - PR #7 merged into `main`.

## Step 2.1: Student, Guardian, and GuardianLink Models (TASK-012)
- **Date:** 2026-09-14
- **Milestone:** Step 2.1 Completed & Merged (PR #8)
- **Details:**
  - Implemented `Student` model (`students` table) with `TenantModel` inheritance, `School` foreign key (`on_delete=models.PROTECT`), `Person` PII vault foreign key (`on_delete=models.PROTECT`), `nisn`, `nis`, `photo_key`, and `status` choices.
  - Implemented student lifecycle state machine (`IAM-019`): `PROSPECT -> ACTIVE -> (INACTIVE | GRADUATED | TRANSFERRED_OUT)`. `transition_status()` validates transitions and publishes transactional `identity.student.status_changed` domain event.
  - Implemented `Guardian` model (`guardians` table) anchored to `Person` PII vault and optional `User` account for mobile phone OTP authentication (`IAM-009`, `IAM-014`).
  - Implemented `GuardianLink` model (`guardian_links` table) representing the relationship between a guardian and a student with composite unique constraint `[foundation_id, guardian, student]`, flags `is_primary`, `can_pickup`, `financial_responsible` (`IAM-015`), and relation types (`FATHER|MOTHER|GUARDIAN`).
  - Created forward migration `apps/identity/migrations/0004_guardian_student_guardianlink_and_more.py`.
  - 5 new automated tests in `apps/identity/tests/test_students.py` testing multi-tenancy isolation, sibling guardian links across schools, state transitions, and soft deletion (total 51 tests passing).
  - PR #8 merged into `main`.

## Step 2.2: Staff Model, Directory API, and Offboarding Lifecycle (TASK-013)
- **Date:** 2026-09-15
- **Milestone:** Step 2.2 Completed & Merged (PR #9)
- **Details:**
  - Implemented `Staff` model (`staff` table) inheriting `core.models.TenantModel` with protected references to `Person` PII vault and `User` account, optional `School` foreign key, `nip`, `employment_type` (`PERMANENT|CONTRACT|HONORARY`), `join_date`, `resignation_date`, `resignation_reason`, and `status` (`ACTIVE|ON_LEAVE|OFFBOARDED`).
  - Implemented atomic staff offboarding service `offboard_staff()` (`IAM-021`): transitions status to `OFFBOARDED`, suspends `User` account, invalidates active sessions within 60 seconds, revokes/soft-deletes `RoleAssignment` entries, publishes `identity.staff.offboarded` domain event with class reassignment payload, and writes an `AuditEvent`.
  - Implemented `StaffViewSet` (`/api/v1/staff/` and `/api/v1/staff/:id/offboard/`) with RBAC enforcement (`school_config.read` / `school_config.write`).
  - Created forward migration `apps/identity/migrations/0005_staff.py`.
  - 3 new automated tests in `apps/identity/tests/test_staff.py` asserting creation, 3-layer tenancy isolation, and offboarding lifecycle execution (total 54 tests passing).
  - PR #9 merged into `main`.

## Step 2.3: Student Directory API and Atomic Bulk XLSX Import (TASK-014)
- **Date:** 2026-09-15
- **Milestone:** Step 2.3 Completed & Merged (PR #10) - Milestone M2 (Student & Staff Records + Bulk Import) 100% Complete
- **Details:**
  - Implemented `StudentBulkImporter` in `apps/identity/importers.py` supporting both Excel (`.xlsx`) and CSV (`.csv`) formats.
  - Implemented strict domain validations: 10-digit NISN format, 16-digit NIK format, E.164 phone normalization, and date parsing across multiple formats.
  - Enforced in-file and database duplicate detection across NISN, NIS, email, and phone.
  - Guaranteed atomic rollback: any error across rows rejects the entire batch and reports the exact offending rows and errors (`spec/02 §8.3`).
  - Added dry-run preview mode (`?dry_run=true`) to parse and validate files without committing database state.
  - Implemented `StudentViewSet` (`/api/v1/students/`) in `apps/identity/views.py` with 3-layer tenancy scoping, status transitions (`IAM-019`), guardian link management (`IAM-014`, `IAM-015`), and bulk import endpoint (`/api/v1/students/import/`).
  - Enforced cross-school isolation returning HTTP 404 (not 403) for students outside authorized school scopes (`spec/02 §8.1`).
  - Verified multi-school parent access enabling parent users to view all linked children across schools under a unified login (`spec/02 §8.2`).
  - 5 new automated tests in `apps/identity/tests/test_student_import_and_api.py` validating all acceptance criteria (total 59 tests passing).
  - PR #10 merged into `main`.

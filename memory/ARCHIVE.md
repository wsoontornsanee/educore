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

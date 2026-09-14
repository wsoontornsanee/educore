# 01_PROJECT — Current Status & Living State

## 1. Project Overview
- **Name:** EduCore (Sistem Operasi Sekolah Swasta)
- **Target Institutions:** Indonesian private schools (sekolah swasta, madrasah, pesantren modern, SPK/National-Plus).
- **Core Stack:** Python 3.12, Django 5.x LTS monolith, MySQL 8.0, cron background scheduling, HTMX + Tailwind for web, React Native (Expo) for mobile.

---

## 2. Current Build Status
- **Current Step:** Step 0 Complete — Moving to Step 1 (`identity`, `foundation`).
- **Target Milestone:** Pilot Core (P0: Milestones M0–M4).
  - *M0 (Step 0) [DONE]:* Django project skeleton, `core` app (`TenantModel`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `drain_tasks`, advisory locks, crontab).
  - *M1 (Step 1) [NEXT]:* Tenancy, auth, RBAC, seed data (`identity`, `foundation`). Foundation admin logs in, sees two schools.
  - *M2 (Step 2):* Student/staff records + bulk XLSX import.
  - *M3 (Step 3):* Credentials, gate attendance events, edge agent stub (`attendance`, `hardware`).
  - *M4 (Step 4):* Notifications service + WhatsApp arrival messaging (`notifications`).

---

## 3. Active Tasks & Immediate Next Actions

| Task ID | Component | Description | Status |
|---|---|---|---|
| `TASK-001` | Memory | Establish agent memory system (`memory/`, `AGENTS.md`, `CLAUDE.md`, `SOUL.md`, `USER.md`) | Completed |
| `TASK-002` | Env & Deps | Configure `requirements.txt` and verify Python environment | Completed |
| `TASK-003` | Django Skeleton | Set up project layout `educore/settings/`, `educore/middleware/`, `manage.py` | Completed |
| `TASK-004` | Core Models | Implement `TenantModel`, `TenantManager`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun` | Completed |
| `TASK-005` | Locking & Tasks | Implement MySQL advisory lock utility and `drain_tasks` management command | Completed |
| `TASK-006` | Verification | Write automated tests for tenancy, money, locks, and cron job logging; run migrations | Completed |
| `TASK-007` | Step 1 Prep | Begin Step 1: `apps/identity/` and `apps/foundation/` models, authentication, RBAC matrix, seed data | Next Up |

---

## 4. Open Technical & Product Decisions

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

## 5. Blockers & Risks
- None currently active.

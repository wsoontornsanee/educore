# 01_PROJECT — Current Status & Living State

## 1. Project Overview
- **Name:** EduCore (Sistem Operasi Sekolah Swasta)
- **Target Institutions:** Indonesian private schools (sekolah swasta, madrasah, pesantren modern, SPK/National-Plus).
- **Core Stack:** Python 3.12, Django 5.x LTS monolith, MySQL 8.0, cron background scheduling, HTMX + Tailwind for web, React Native (Expo) for mobile.
- **Design System:** `spec/17-design-system-and-ui.md` (Plus Jakarta Sans + IBM Plex Sans/Mono, Merah flag-saturated `#C8102E`, institutional square 0px radii, 5 mandatory screen states).

---

## 2. Current Build Status
- **Current Step:** Step 0 Complete — Ready for Step 1 (`identity`, `foundation`).
- **Target Milestone:** Pilot Core (P0: Milestones M0–M4).
  - *M0 (Step 0) [DONE]:* Django project skeleton, `core` app (`TenantModel`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `drain_tasks`, advisory locks, crontab).
  - *M1 (Step 1) [NEXT UP]:* Tenancy, auth, RBAC, seed data (`identity`, `foundation`). Demo: Foundation admin logs in, sees two schools.
  - *M2 (Step 2):* Student/staff records + bulk XLSX import (`identity`).
  - *M3 (Step 3):* Credentials, gate attendance events, edge agent stub (`attendance`, `hardware`).
  - *M4 (Step 4):* Notifications service + WhatsApp arrival messaging (`notifications`).

---

## 3. Step 0 Retrospective & Key Architectural Discoveries

1. **Fail-Closed Tenancy:** `TenantManager.get_queryset()` was designed to return `.none()` if no active thread-local `foundation_id` is set. This guarantees zero cross-tenant data leaks if an endpoint or command forgets to scope context.
2. **Float Rejection in MoneyField:** Validating at both `to_python` and `get_prep_value` prevents any accidental Python `float` assignment, ensuring all financial calculations retain strict decimal precision (`CUR-005`).
3. **Database Driver Agnosticism for Dev/CI:** Integrated `pymysql.install_as_MySQLdb()` fallback in `educore/__init__.py` allowing seamless development on platforms without precompiled C-wheels for `mysqlclient`, alongside `docker-compose.yml` for local MySQL 8.
4. **Advisory Lock Safety:** Named MySQL locks (`GET_LOCK`) cleanly prevent overlapping cron job executions across horizontal app nodes. Fallback in-memory mutex allows offline tests to verify locking semantics without external services.
5. **Worker Task Draining:** `drain_tasks` uses `SELECT ... FOR UPDATE SKIP LOCKED` where supported (MySQL 8) and safely executes tasks under the task's originating `foundation_id` context.

---

## 4. Active Tasks & Immediate Next Actions (Step 1)

| Task ID | Component | Description | Status |
|---|---|---|---|
| `TASK-001` | Core Setup | Step 0 Skeleton & Foundation primitives | Completed |
| `TASK-008A`| Identity Core | Implement `Foundation` and `School` models + `seed_demo_foundation` | Completed |
| `TASK-008B`| Identity Auth | Implement `User` model, `Person` (PII vault), and authentication services | Next Up |
| `TASK-009` | Foundation App | Implement `apps/foundation/` portal models and school management views (`spec/03`) | Pending |
| `TASK-010` | RBAC & Auth | Enforce role permissions matrix, session auth for web, JWT for mobile (`spec/02 §3, §4`) | Pending |
| `TASK-011` | Entitlements | Implement `foundation_entitlements` gating (`spec/02 §6`) | Pending |

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

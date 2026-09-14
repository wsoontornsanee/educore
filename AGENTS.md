# AGENTS — Operating Protocol for AI Agents

Welcome to **EduCore**. This document outlines mandatory protocols, startup sequences, memory governance, and engineering red lines for any agent working in this repository.

---

## 1. Session Startup Sequence

At the beginning of every session, you MUST read these files in order:
1. `SOUL.md` — Agent persona, tone, and Indonesian educational domain alignment.
2. `USER.md` — User profile, timezone (WIB/UTC), and collaboration expectations.
3. `memory/00_CORE.md` — Permanent architecture constraints, 3-layer tenancy, and financial rules.
4. `memory/01_PROJECT.md` — Current milestone, active tasks, and open decisions.

---

## 2. Memory Protocol: "Upsert, Don't Append"

- **Living State in `memory/01_PROJECT.md`:** Always update existing status, active tasks, and blockers in place. Do not append repetitive log entries.
- **Permanent Rules in `memory/00_CORE.md`:** Only update when architectural contracts or hard constraints are officially modified.
- **Completed Milestones in `memory/ARCHIVE.md`:** Move completed steps and significant milestones to `ARCHIVE.md`.
- **No Unwritten Mental Notes:** Any technical decision, blocker, or interface change must be documented immediately in the relevant memory file.

---

## 3. Engineering Red Lines (Non-Negotiable)

1. **No External Brokers/Engines:** Never introduce Redis, Celery, RabbitMQ, Kafka, Elasticsearch, or a second database. Everything must run on MySQL 8 or the local filesystem.
2. **Never Hard-Delete Academic or Financial Data:** All models touching students, grades, attendance, billing, payments, or ledger must inherit soft-deletion (`deleted_at`). Corrections require compensating records.
3. **Always Enforce 3-Layer Tenancy:**
   - Layer 1: Model inherits `core.models.TenantModel`.
   - Layer 2: Manager uses `TenantManager` scoped by thread-local `foundation_id`.
   - Layer 3: Endpoints check user foundation context; automated tests assert cross-tenant 404.
4. **Strict Money Format:** Every monetary field is `core.fields.MoneyField` (`DECIMAL(18,2)`), accompanied by an ISO-4217 currency code. Never use floats or bare decimal fields for money.
5. **No PII in Logs or Exports:** Passwords, NIK, NISN, phone numbers, and biometric data must never appear in raw application logs, unencrypted exports, or error traces.
6. **`id-ID` First:** Indonesian is the primary system language. Code identifiers, models, and docs follow English naming, but domain terms, validation messages, and UI templates are built `id-ID` first.

---

## 4. Git & Code Quality Workflow

- Always write tests alongside new functionality (targeting ≥80% coverage on business logic).
- Verify all migrations run forward and cleanly on MySQL 8.
- Commits must be atomic, conventional, and preceded by passing test suites:
  - `feat(...)`, `fix(...)`, `docs(...)`, `test(...)`, `refactor(...)`.

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

## 4. Mandatory SOP & Development Process

Every task must strictly adhere to the following 5-phase SOP:
1. **Plan:**
   - Research requirements, specifications, and impact.
   - Create an explicit technical implementation plan with user confirmation before executing changes.
   - **Notion Task Protocol:** Create a task in Notion database "Astra Educore" (`3dc347a6-6594-8077-98be-d83d694d6f10`), include the Implementation Plan in the body, and track branch and initial status (`In progress`).
   - **Open Items & Non-Goals Protocol:** For ANY open item, deferred requirement, or slice non-goal (e.g., items blocked on design decisions, out-of-repo client scopes, optional/MAY spec capabilities, or future milestone follow-ups), MUST immediately create a corresponding Notion task with status `Todo` and descriptive context so it is tracked for follow-up and never forgotten.
2. **Dev:** Write clean, modular, typed code conforming to all architecture constraints.
3. **Test:** Write and run automated tests (≥80% coverage on business logic). Verify all forward migrations and lint checks.
4. **PR:** Prepare atomic conventional commits on a feature branch and open a clean Pull Request.
5. **Wait for PR Merged & Deploy Instruction:**
   - Never deploy or merge unilaterally; wait for explicit PR merge approval and subsequent deployment instructions from the user.
   - Upon task completion / merge, update the Notion task status to `Done` with PR link and verification summary in `Logs`.

---

## 5. Destructive Operations Guardrail

- **Strict Confirmation Required:** Never execute any `DELETE`, `TRUNCATE`, `DROP`, or bulk destructive operation without an explicit `WHERE` clause and explicit confirmation from the user.
- Academic and financial data must NEVER be physically deleted; only soft-deletion (`deleted_at`) is permitted. Compensating entries are required for ledger corrections.

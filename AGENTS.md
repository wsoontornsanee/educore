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
   - **Claiming an existing task:** the moment an existing `Todo` item is chosen to work on — before any research, planning, or coding — set its Status to `In progress` (and `Branch`). Concurrent sessions pick work from `Todo`; this is the lock that stops two sessions taking the same item.
   - **Open Items & Non-Goals Protocol:** For ANY open item, deferred requirement, or slice non-goal (e.g., items blocked on design decisions, out-of-repo client scopes, optional/MAY spec capabilities, or future milestone follow-ups), MUST immediately create a corresponding Notion task with status `Todo` and descriptive context so it is tracked for follow-up and never forgotten.
2. **Dev:** Write clean, modular, typed code conforming to all architecture constraints.
3. **Test:** Write and run automated tests (≥80% coverage on business logic). Verify all forward migrations and lint checks.
4. **PR:**
   - Always fetch and merge/rebase on the latest target branch (`main`) so you have the latest code before opening or finalizing a PR.
   - Verify that the PR is strictly mergeable without conflicts, with all automated tests passing.
   - Prepare atomic conventional commits on a feature branch and open a clean Pull Request.
   - Strictly follow the **Pre-PR Synchronization & Conflict Resolution Protocol** below.
5. **Wait for PR Merged & Deploy Instruction:**
   - Never deploy or merge unilaterally; wait for explicit PR merge approval and subsequent deployment instructions from the user.
   - Upon task completion / merge, update the Notion task status to `Done` with PR link and verification summary in `Logs`.

### PRE-PR SYNCHRONIZATION & CONFLICT RESOLUTION PROTOCOL

Before opening, marking ready, or updating any Pull Request, you must ensure your branch is cleanly rebased on top of the latest target branch and conflict-free.

#### Mandatory Pre-PR Checklist:

1. Fetch and Rebase:
   - Run: `git fetch origin main` (or the relevant target base branch).
   - Run: `git rebase origin/main`.

2. Resolve In-Flight Merge Conflicts:
   - If conflicts occur, inspect each conflicting file directly.
   - PRESERVATION RULE: You must NEVER delete, rewrite, or discard existing functionality or business logic introduced by other commits. Blend the changes: keep all upstream logic intact while integrating your task changes.
   - Ensure zero conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) remain anywhere in the code.
   - Stage resolved files: `git add <resolved-files>`
   - Finalize: `git rebase --continue` (Repeat until rebase is clean).

3. Targeted Validation (DO NOT Run Full Suite):
   - DO NOT run the entire project test suite, full regression suite, or end-to-end integration passes during conflict resolution.
   - ONLY run:
     1. Syntax/Linter/Build check on the affected files (e.g., `python -m py_compile <file>` or `tsc --noEmit`).
     2. Specific unit tests directly targeting the conflicting modules or modified functions (e.g., `pytest path/to/test_modified_module.py`).
   - If targeted tests fail, fix the integration without touching unrelated files.

4. Push & PR Creation:
   - Only after targeted tests pass cleanly, force-push with lease:
     `git push origin <your-feature-branch> --force-with-lease`
   - You are now cleared to open or update the PR. Never open a PR if `origin/main` has diverged.

---

## 5. Destructive Operations Guardrail

- **Strict Confirmation Required:** Never execute any `DELETE`, `TRUNCATE`, `DROP`, or bulk destructive operation without an explicit `WHERE` clause and explicit confirmation from the user.
- Academic and financial data must NEVER be physically deleted; only soft-deletion (`deleted_at`) is permitted. Compensating entries are required for ledger corrections.

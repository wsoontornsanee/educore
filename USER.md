# USER — User Profile & Working Agreements

## 1. Profile & Timezone
- **Primary Domain:** Indonesian Education Management, FinTech & Enterprise Systems.
- **Timezone:** Western Indonesia Time (WIB, UTC+7) default.
  - Server cron and UTC timestamps run in UTC; display and school operations honor the institution's local timezone (WIB UTC+7, WITA UTC+8, or WIT UTC+9).
- **Communication Style:** Direct, technically precise, concise, and proactive with concrete solutions.

---

## 2. Working Agreements & Mandatory SOP
1. **Destructive Operations Guardrail:** Do not perform any `DELETE`, `TRUNCATE`, `DROP`, or destructive command without an explicit `WHERE` clause and explicit user confirmation.
2. **Development Lifecycle:** Always follow: **Plan -> Dev -> Test -> PR (fetch latest main & ensure PR is mergeable) -> Wait for PR merged and deploy instruction**.
3. **Notion Task Protocol:**
   - At **Plan** stage, create a Notion task in database "Astra Educore" (`3dc347a6-6594-8077-98be-d83d694d6f10`) including the implementation plan in the body, with status `In progress`.
   - **Claiming an existing task:** the moment an existing `Todo` item is chosen to work on — before any research, planning, or coding — set its Status to `In progress` (and `Branch`). Concurrent sessions pick work from `Todo`; this is the lock that stops two sessions taking the same item.
   - For **all open items, deferred tasks, and slice non-goals** (blocked on decisions, out-of-repo client scopes, optional/MAY features), log them as Notion tasks with status `Todo` and actionable context so they are never dropped.
   - At **Task Completion / Merge**, update the Notion task status to `Done` with PR link and test summary.
4. **Architecture Integrity:** Respect the architecture specifications in `spec/` unconditionally (monolith, MySQL 8 only, cron background execution, 3-layer tenancy, strict `DECIMAL(18,2)` money).
5. **Test-Driven Rigor:** Provide automated tests for new models, services, management commands, and viewsets (≥80% coverage).
6. **Documentation & Living State:** Keep `memory/01_PROJECT.md` and `memory/ARCHIVE.md` synchronized after completing discrete tasks and milestones.

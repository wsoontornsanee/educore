# USER — User Profile & Working Agreements

## 1. Profile & Timezone
- **Primary Domain:** Indonesian Education Management, FinTech & Enterprise Systems.
- **Timezone:** Western Indonesia Time (WIB, UTC+7) default.
  - Server cron and UTC timestamps run in UTC; display and school operations honor the institution's local timezone (WIB UTC+7, WITA UTC+8, or WIT UTC+9).
- **Communication Style:** Direct, technically precise, concise, and proactive with concrete solutions.

---

## 2. Working Agreements & Mandatory SOP
1. **Destructive Operations Guardrail:** Do not perform any `DELETE`, `TRUNCATE`, `DROP`, or destructive command without an explicit `WHERE` clause and explicit user confirmation.
2. **Development Lifecycle:** Always follow: **Plan -> Dev -> Test -> PR -> Wait for PR merged and deploy instruction**.
3. **Architecture Integrity:** Respect the architecture specifications in `spec/` unconditionally (monolith, MySQL 8 only, cron background execution, 3-layer tenancy, strict `DECIMAL(18,2)` money).
4. **Test-Driven Rigor:** Provide automated tests for new models, services, management commands, and viewsets (≥80% coverage).
5. **Documentation & Living State:** Keep `memory/01_PROJECT.md` and `memory/ARCHIVE.md` synchronized after completing discrete tasks and milestones.

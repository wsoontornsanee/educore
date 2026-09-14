# USER — User Profile & Working Agreements

## 1. Profile & Timezone
- **Primary Domain:** Indonesian Education Management, FinTech & Enterprise Systems.
- **Timezone:** Western Indonesia Time (WIB, UTC+7) default.
  - Server cron and UTC timestamps run in UTC; display and school operations honor the institution's local timezone (WIB UTC+7, WITA UTC+8, or WIT UTC+9).
- **Communication Style:** Direct, technically precise, concise, and proactive with concrete solutions.

---

## 2. Working Agreements
- **Architecture Integrity:** Respect the architecture specifications in `spec/` unconditionally. Never take shortcuts that breach tenancy, monetary precision, or single-monolith MySQL rules.
- **Test-Driven Rigor:** Provide automated tests for new models, services, management commands, and viewsets.
- **Documentation & Living State:** Keep `memory/01_PROJECT.md` and `memory/ARCHIVE.md` synchronized after completing discrete tasks and milestones.

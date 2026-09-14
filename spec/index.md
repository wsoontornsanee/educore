# EduCore Indonesia — Specification Index

Source of truth for the EduCore build. Each module is a self-contained spec file
intended to be fed to Claude Code one at a time (or as a set).

## Reading order

| # | File | Module | Depends on |
|---|------|--------|-----------|
| 00 | `00-overview.md` | Product overview, glossary, personas | — |
| 01 | `01-platform-architecture.md` | Stack, services, environments, conventions | 00 |
| 02 | `02-identity-and-access.md` | Auth, tenancy, RBAC | 01 |
| 03 | `03-foundation-portal.md` | Yayasan multi-campus governance | 02 |
| 04 | `04-academic.md` | Gradebook, exams, timetable, homework | 02 |
| 05 | `05-attendance-and-safety.md` | Gates, RFID/face, pickup, bus | 02, 12 |
| 06 | `06-finance-and-billing.md` | SPP invoicing, VA/QRIS, reconciliation | 02 |
| 07 | `07-canteen-and-wallet.md` | Stored-value wallet, POS, nutrition | 06, 12 |
| 08 | `08-parent-app.md` | Mobile client | 04, 05, 06, 07 |
| 09 | `09-teacher-suite.md` | Teacher web + mobile client | 04, 05, 10 |
| 10 | `10-campus-life.md` | UKS clinic, behaviour, e-library, counselling | 02 |
| 11 | `11-hr-and-payroll.md` | Staff, PPh 21 TER, BPJS | 02 |
| 12 | `12-hardware-and-iot.md` | Device protocol, edge agent, provisioning | 01 |
| 13 | `13-notifications.md` | WhatsApp/push/email delivery | 01 |
| 14 | `14-compliance-and-integrations.md` | UU PDP, DAPODIK/EMIS, gateways | 01 |
| 15 | `15-reporting-and-analytics.md` | Rollup tables, dashboards, exports | all |
| 16 | `16-currency-and-money.md` | Multi-currency, DECIMAL(18,2), display contract | 01 |
| 17 | `17-design-system-and-ui.md` | Design system, UI components, typography, colours, mockups | 01 |

## Architecture in one paragraph

**A single Django monolith.** Every module below is a Django app inside one
Django project, in one repository that also contains all frontend code. **MySQL 8
is the only infrastructure dependency** — no Redis, no Celery, no broker, no
object store. **All background work is a management command run by system cron**
(`deploy/crontab`). Read spec 01 before writing any code; it overrides anything
elsewhere that implies otherwise.

## Spec conventions

- `MUST` / `SHOULD` / `MAY` per RFC 2119.
- Every requirement is tagged `[MOD-NNN]` and is individually testable.
- **Money is `DECIMAL(18,2)` + an ISO-4217 `currency` code — always. Never float,
  never integer minor units.** IDR is stored with `.00` and displayed by the
  frontend without decimals (`Rp 1.500.000`). Full rules in spec 16.
- Where an older draft wrote `amount_idr bigint`, read `amount DECIMAL(18,2)` plus `currency`.
- All timestamps stored UTC, rendered in the tenant's timezone
  (`Asia/Jakarta` | `Asia/Makassar` | `Asia/Jayapura`).
- All list endpoints are cursor-paginated: `?cursor=&limit=` → `{data, next_cursor}`.
- All write endpoints accept `Idempotency-Key` header.
- No feature may depend on WebSockets, a message broker, or a long-running worker.

# EduCore

EduCore is a multi-tenant school operating system for Indonesian schools of all types (sekolah negeri, sekolah swasta, madrasah, pesantren modern, National-Plus/SPK).

## Specifications

Detailed module specifications and architecture requirements are maintained under [`spec/`](./spec/):

- [`spec/index.md`](./spec/index.md) — Specification index, reading order, and global conventions.
- [`spec/00-overview.md`](./spec/00-overview.md) — Product overview, personas, tenancy model, Indonesian domain glossary.
- [`spec/appendix.md`](./spec/appendix.md) — Suggested implementation sequence and non-negotiable engineering rules.

## Local Development Setup

`docker-compose up` starts a MySQL 8 container for local development. Its `deploy/mysql-init/` scripts run automatically on first init and load MySQL's named-timezone tables (`mysql.time_zone_name`) — required because `settings.TIME_ZONE='Asia/Jakarta'` (`USE_TZ=True`), and MySQL's own timezone data isn't loaded by default on a fresh install or the official `mysql:8.0` image.

**Why this matters**: without those tables, `CONVERT_TZ('...', 'UTC', 'Asia/Jakarta')` silently returns `NULL` — no error. Django compiles every `__date` lookup on a `DateTimeField` (e.g. `occurred_at__date`) through exactly that call whenever `TIME_ZONE` isn't UTC, so the day-bucketed reporting rollups in `apps/reporting/services.py` (`refresh_wallet_activity`, `refresh_daily_finance`, `refresh_daily_attendance`, `refresh_ar_aging`) silently write **zero rows** instead of raising.

If you're connecting to a MySQL instance you set up yourself (not via `docker-compose up` from a clean volume — the init script only runs once, against an empty data directory), or an existing volume from before this was added, load the tables manually:

```bash
mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -u root -p mysql
```

Verify it worked:

```sql
SELECT CONVERT_TZ(NOW(), 'UTC', 'Asia/Jakarta');  -- must NOT be NULL
```

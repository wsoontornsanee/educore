# 01 — Platform Architecture

## 1. Hard constraints (non-negotiable)

These three rules override anything else in the spec set. If another file
contradicts them, these win.

1. **One Django monolith.** Every module in this spec set is a Django *app*
   inside a single Django project, in a single repository, deployed as a single
   unit. No microservices, no separate API gateway, no extracted workers.
2. **MySQL is the only infrastructure dependency — everywhere.** No Redis, no
   Celery/RabbitMQ, no Elasticsearch, no S3, no message broker, no external cache,
   and no second database engine. Anything those would normally provide is
   implemented on MySQL or the local filesystem. This extends to the on-premise
   campus gateway, which runs its own local MySQL 8 rather than SQLite (spec 12 §3).
3. **All background work is a Django management command driven by system cron.**
   No long-running worker processes, no in-process schedulers.

Frontend and backend live in the **same repository** and are served by the same
Django project.

## 2. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | One language server-side |
| Framework | Django 5.x LTS | Project `educore`, one app per spec module |
| Database | **MySQL 8.0** (InnoDB, `utf8mb4`) | The only external service |
| API | Django REST Framework | For mobile + JS clients |
| Server-rendered UI | Django templates + HTMX + Alpine.js | Admin, foundation portal, teacher web, POS, kiosk |
| Styling | Tailwind CSS, compiled to a static file at build time | No Node runtime in production |
| Mobile (parent + teacher) | React Native (Expo) in `/mobile` of the same repo | Talks to DRF |
| Auth | Django sessions for web; DRF token/JWT (`djangorestframework-simplejwt`) for mobile | |
| Files | Django `FileSystemStorage` on a server volume | Served by nginx, never by Django |
| Background jobs | Management commands + system cron | See §5 |
| Realtime | HTTP polling / long-poll (see §6) | No WebSocket server |
| Search | MySQL `FULLTEXT` indexes + `LIKE` on indexed prefixes | |
| Cache | Django `DatabaseCache` (`django_cache` table) or per-request local memory | No Redis |
| i18n | Django i18n, `id-ID` default, `en-US` secondary | `.po` files in repo |

**Allowed Python dependencies** (pure-Python, no service required):
`django`, `djangorestframework`, `djangorestframework-simplejwt`, `mysqlclient`,
`django-htmx`, `Pillow`, `openpyxl` (XLSX), `weasyprint` (PDF), `requests`,
`cryptography`, `python-dateutil`. Adding anything that needs a running service
requires an explicit architecture decision record.

## 3. Repository layout

```
/
├── manage.py
├── requirements.txt
├── educore/                     # project: settings, urls, wsgi, middleware
│   ├── settings/{base,local,staging,production}.py
│   └── middleware/{tenancy,audit,timezone}.py
├── apps/
│   ├── core/                    # base models, money field, mixins, cron registry
│   ├── identity/                # spec 02
│   ├── foundation/              # spec 03
│   ├── academic/                # spec 04
│   ├── attendance/              # spec 05
│   ├── finance/                 # spec 06
│   ├── wallet/                  # spec 07
│   ├── campuslife/              # spec 10
│   ├── payroll/                 # spec 11
│   ├── hardware/                # spec 12
│   ├── notifications/           # spec 13
│   ├── compliance/              # spec 14
│   └── reporting/               # spec 15
├── frontend/
│   ├── templates/               # Django templates, per app
│   ├── static/{css,js,img}/
│   └── tailwind/                # build-time only
├── mobile/                      # React Native (parent app spec 08, teacher mobile spec 09)
├── deploy/
│   ├── nginx.conf
│   ├── gunicorn.service
│   └── crontab                  # the single source of truth for scheduled jobs
└── docs/specs/                  # this spec set
```

### 3.1 App boundaries
Each Django app owns its models. **Cross-app access goes through the owning app's
`services.py` — never by importing another app's models directly or writing to
its tables.** A CI check (import-linter rules) enforces the allowed dependency graph:

```
core  <- everything
identity <- all business apps
finance <- wallet, campuslife, payroll, reporting
attendance <- academic, reporting
notifications <- all (one-way: apps call notifications, notifications calls nobody)
reporting <- nothing (reads via its own rollup tables only)
```

## 4. Multi-tenancy

Single database, shared schema, `foundation_id` on every tenant table.
MySQL has no row-level security, so tenancy is enforced in **three** layers:

| ID | Requirement |
|---|---|
| `ARC-001` | Every tenant model MUST inherit `core.models.TenantModel` (adds `foundation`, `created_at/by`, `updated_at/by`, `deleted_at`). |
| `ARC-002` | `TenantModel.objects` MUST be a `TenantManager` whose default queryset filters by the current request's foundation, read from a thread-local set by `TenancyMiddleware`. Unscoped access requires the explicit `all_tenants` manager, which is only importable from admin/ops code. |
| `ARC-003` | A CI test MUST assert that every model inheriting `TenantModel` has a `foundation` FK with `db_index=True` and a composite index starting with `foundation_id` on its primary query paths. |
| `ARC-004` | Every DRF viewset MUST derive tenancy from the authenticated user, never from a request parameter. A test harness MUST attempt cross-tenant access on every registered viewset and expect 404. |
| `ARC-005` | Management commands MUST take `--foundation` explicitly, or iterate foundations deliberately; a command that runs unscoped by accident is a bug. |

## 5. Background jobs — cron only

| ID | Requirement |
|---|---|
| `ARC-006` | Every scheduled task MUST be a Django management command in the owning app's `management/commands/`, runnable idempotently and safe to re-run. |
| `ARC-007` | Every command MUST take a MySQL **named advisory lock** (`GET_LOCK('educore:<job>', 0)`) for its whole run and exit cleanly if the lock is held. Overlapping runs are never allowed. |
| `ARC-008` | Every run MUST write a `core.JobRun` row: job name, started_at, finished_at, status, items_processed, error text. A job that has not succeeded within its expected window MUST raise an alert on the ops dashboard. |
| `ARC-009` | Long jobs MUST process in bounded batches with `--limit` and resume from a stored cursor, so a cron slot can never run unbounded. |
| `ARC-010` | Asynchronous work triggered by user actions (send a message, generate PDFs, build an export) MUST be written to a `core.TaskQueue` MySQL table and drained by a `drain_tasks` command running every minute. Rows carry `run_after`, `attempts`, `max_attempts`, `locked_at`, `locked_by`, `status`. |
| `ARC-011` | `drain_tasks` MUST claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` so multiple app servers can drain safely. |
| `ARC-012` | Failed tasks MUST retry with exponential backoff (1, 5, 15, 60 min) and land in a dead-letter state after `max_attempts`, visible in Django admin with a one-click requeue. |

### 5.1 Crontab (`deploy/crontab` — canonical)

```cron
* * * * *      educore drain_tasks --limit 200            # generic async queue
* * * * *      educore ingest_device_events               # edge agent event intake sweep
*/5 * * * *    educore refresh_reporting --scope=dashboard
*/5 * * * *    educore sync_payment_status                # poll pending payment intents
*/10 * * * *   educore check_device_health                # offline device alerts
*/15 * * * *   educore send_due_notifications             # scheduled + quiet-hours release
0 * * * *      educore expire_sessions
30 6 * * *     educore mark_absent_students               # after absent_cutoff per school
0 17 * * *     educore send_digests
0 18 * * *     educore reconcile_payments --date=today
0 19 * * *     educore reconcile_wallet_balances
0 20 * * *     educore refresh_reporting --scope=full
0 21 * * *     educore run_arrears_ladder
0 22 * * *     educore enforce_retention --dry-run=false
0 23 * * *     educore backup_database
25 2 25 * *    educore generate_invoices --next-period
0 3 1 * *      educore close_metering_month
0 4 * * 0      educore settle_merchants
```

| ID | Requirement |
|---|---|
| `ARC-013` | `deploy/crontab` MUST be the only place schedules are defined, and MUST be deployed as-is. No schedule may live in application code. |
| `ARC-014` | Every command in the crontab MUST honour the school's timezone internally; cron itself runs in UTC on the server. |

## 6. Realtime without a socket server

| ID | Requirement |
|---|---|
| `ARC-015` | The live gate console MUST poll `GET /api/v1/gate/live?since=<cursor>` every 3 seconds, returning only events after the cursor. The endpoint MUST be a single indexed query on `(school_id, id)` and MUST return in <50ms. |
| `ARC-016` | POS terminals MUST poll their sync endpoint every 60 seconds; rule changes therefore take effect within 60s (satisfies `WAL-012`). |
| `ARC-017` | Parent app MUST rely on push notifications (spec 13) plus pull-to-refresh, not a persistent connection. |
| `ARC-018` | No feature may require sub-second server push. If a requirement elsewhere implies one, it MUST be redesigned around polling. |

## 7. Environments

| Env | Purpose | Composition |
|---|---|---|
| `local` | Development | Django runserver + local MySQL 8; seeded fixture foundation `Yayasan Demo` |
| `staging` | Demos + pilot UAT | One VM: nginx + gunicorn + MySQL; anonymised data, refreshed weekly by cron |
| `production` | Live | Indonesian region: nginx + gunicorn (N workers) + MySQL primary with replica, one cron host |

**Only one host runs cron.** The cron host MUST be identified by an env var
`EDUCORE_CRON_HOST=1`; commands refuse to run elsewhere unless `--force`.

## 8. Conventions

### 8.1 API
- Base `/api/v1/`. Versioned by path.
- Errors: DRF exception handler rendering RFC 9457 `application/problem+json`:
  ```json
  { "type":"https://errors.educore.id/insufficient-balance",
    "title":"Insufficient wallet balance", "status":409,
    "detail":"Balance 12000.00, required 18000.00",
    "instance":"/api/v1/pos/transactions", "code":"WALLET_INSUFFICIENT" }
  ```
- `code` is a stable SCREAMING_SNAKE string; clients branch on `code`, never prose.
- Cursor pagination on all list endpoints: `?cursor=&limit=` → `{results, next_cursor}`.
- All mutating endpoints accept `Idempotency-Key`; `core.IdempotencyRecord` stores
  (key, endpoint, request_hash, response_body, status) with a unique constraint and
  a 7-day cron sweep.

### 8.2 Database
- `snake_case`, plural table names, explicit `db_table` on every model.
- Primary keys: `BigAutoField` where insertion order is useful (events, ledger),
  `CHAR(36)` UUID4 for anything exposed in URLs or generated client-side (device
  events, POS transactions, idempotency keys). **Do not use UUID PKs on high-write
  tables** — InnoDB clustered-index fragmentation is a real cost at gate-event volume.
- Soft delete via `deleted_at`; academic and financial rows are never hard-deleted.
- Migrations forward-only; shipped migrations are never edited.
- `utf8mb4_0900_ai_ci` collation throughout.
- Every FK explicitly `on_delete=PROTECT` for financial/academic references,
  `CASCADE` only for genuinely owned child rows.

### 8.3 Money
See **spec 16 — Currency and Money**. Summary: every monetary column is
`DECIMAL(18,2)` paired with a `currency` char(3). No floats anywhere, ever.

### 8.4 Events
No message bus. Domain events are rows in `core.DomainEvent`
(`id, foundation_id, name, payload JSON, occurred_at, processed_at`), written in
the **same transaction** as the state change. `drain_tasks` dispatches them to
registered in-process handlers. Handlers MUST be idempotent on `event_id`.

Names: `module.entity.verb` — `attendance.gate_scan.recorded`,
`finance.invoice.issued`, `finance.payment.settled`, `wallet.transaction.completed`,
`academic.report_card.published`.

### 8.5 Audit
Append-only `core.AuditEvent`: actor, role, foundation, school, ip, action,
entity type/id, before/after JSON diff (PII redacted), timestamp. Written by an
explicit `audit()` service call — not by signals, which are too easy to miss in
review. Retention 7 years. Every finance, grade-change and PII-export action audits.

## 9. Non-functional requirements

| ID | Requirement |
|---|---|
| `NFR-001` | API p95 < 400ms, p99 < 900ms at 200 rps sustained |
| `NFR-002` | Gate-event ingest p99 < 150ms; parent notification queued < 5s, dispatched by the next `send_due_notifications` tick or immediately for `EMERGENCY` |
| `NFR-003` | 99.9% uptime 05:00–19:00 WIB; maintenance Sunday 01:00–04:00 WIB |
| `NFR-004` | Nightly `mysqldump` + binlog retention for PITR; restore drill quarterly; RPO 15m / RTO 4h |
| `NFR-005` | Supports a 3,000-student campus: 6,000 gate events and 2,500 POS transactions/day |
| `NFR-006` | All clients work on Android 9+ over 3G-class links (≤1.5 Mbps, 300ms RTT) |
| `NFR-007` | Parent app cold start < 3s on a mid-range Android device |
| `NFR-008` | PII encrypted at rest (application-level AES-256 via `cryptography` for sensitive columns; MySQL tablespace encryption for the rest). Biometric templates use a separate key. |
| `NFR-009` | High-write tables (`gate_events`, `wallet_transactions`, `pos_transactions`, `audit_events`, `domain_events`) MUST be partitioned by month or archived by a cron job to keep the hot set small |

## 10. Definition of done (every module)

1. Migrations written, reversible, and applied cleanly to an empty MySQL 8.
2. Model tests + service-layer tests ≥80% line coverage on business rules.
3. DRF/HTMX view tests for every endpoint listed in the module spec.
4. Every `[MOD-NNN]` requirement mapped to at least one test.
5. Cross-tenant access test passes for every new viewset.
6. Any new scheduled work added to `deploy/crontab` **and** registered in `core.JobRun`.
7. `audit()` called on every mutating action.
8. `id-ID` translations complete; no hardcoded English in templates.
9. Seed data extended so the module is demoable in `local`.

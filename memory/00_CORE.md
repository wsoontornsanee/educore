# 00_CORE — Permanent Architecture & Rules

## 1. Non-Negotiable Hard Constraints

These three rules override anything else in the spec set. If another file contradicts them, these win (`spec/01-platform-architecture.md §1`).

1. **One Django Monolith:**
   - Every module is a Django app inside a single Django project (`educore`), in a single repository, deployed as a single unit.
   - No microservices, no separate API gateway, no extracted workers.
2. **MySQL 8.0 is the Only Infrastructure Dependency — Everywhere:**
   - MySQL 8.0 (InnoDB, `utf8mb4_0900_ai_ci`) is the only external service.
   - Strictly NO Redis, NO Celery / RabbitMQ, NO Elasticsearch, NO S3, NO message brokers, NO external caches, and NO second database engine.
   - Everything those would normally provide is implemented on MySQL or the local filesystem (e.g., `DatabaseCache` for cache, local filesystem for media served by Nginx, database tables for queues and locks).
   - The on-premise campus gateway also runs local MySQL 8.
3. **All Background Work is Driven by System Cron:**
   - All background work is implemented as Django management commands triggered by system cron (`deploy/crontab`).
   - No long-running background worker processes, no in-process schedulers.
   - Cron host runs with `EDUCORE_CRON_HOST=1`.

---

## 2. Multi-Tenancy Architecture (3-Layer Isolation)

Single database, shared schema, `foundation_id` on every tenant table (`spec/01 §4`, `spec/02`).
Because MySQL lacks row-level security (RLS), tenancy is strictly enforced across three layers:

- **Layer 1 (Model Layer):** Every tenant model inherits from `core.models.TenantModel`. This guarantees columns: `foundation_id`, `created_at`, `created_by`, `updated_at`, `updated_by`, and `deleted_at`.
- **Layer 2 (Manager Layer):** `TenantModel.objects` uses `TenantManager`, filtering automatically by the current thread-local `foundation_id` set by `TenancyMiddleware`. If no foundation context is set, queries fail-closed (`none()`). Unscoped access is permitted only via `TenantModel.all_tenants` (restricted to internal ops/admin scripts).
- **Layer 3 (Viewset & CI Test Layer):** Every DRF viewset derives tenancy strictly from the authenticated user token/session, never from request URL parameters. Automated cross-tenant test harnesses verify that cross-tenant access returns 404. CI tests verify that every `TenantModel` subclass has an indexed `foundation_id` and composite indexes prefixed by `foundation_id`.

---

## 3. Currency and Money Rules

From `spec/16-currency-and-money.md`:

1. **Storage Specification:**
   - Every monetary column uses `core.fields.MoneyField` (`DECIMAL(18,2)`).
   - Paired with an ISO-4217 currency code (`CHAR(3)`). Default base and reporting currency: `IDR`.
   - Never use `FloatField`, `DoubleField`, bare `DecimalField`, or integer minor units for monetary figures.
2. **Double-Entry Ledger:**
   - Financial journals must balance to `0.00` per journal per currency. Single-sided adjustments are forbidden.
3. **Frontend Formatting Contract:**
   - The API always returns `{"amount": "1500000.00", "currency": "IDR"}` as a string, never a formatted value or float (`CUR-026`).
   - For `IDR`, UI renders `Rp 1.500.000` (Indonesian grouping, no decimals shown, even though stored as `1500000.00`).
   - For non-IDR, UI renders 2 decimal places with locale grouping.
   - Clients never sum money values; all totals are calculated server-side.
4. **Rounding Rules:**
   - Python `Decimal` with `ROUND_HALF_UP` applied once at persistence.
   - Splitting amounts into parts must use the largest-remainder method so components sum exactly to the total.
   - IDR invoice totals round to Rp 100 via an explicit `PEMBULATAN` line item.

---

## 4. Non-Negotiable Engineering Rules

From `spec/appendix.md §2`:

1. **Tenancy in 3 layers:** `TenantModel` + `TenantManager` + per-viewset cross-tenant test. Tenant tables without `foundation` FK fail CI.
2. **Money is `DECIMAL(18,2)` + currency, and double-entry:** No float, no integer minor units, no single-sided adjustments.
3. **No hard-deletion of academic or financial data:** Soft deletes via `deleted_at` only. Corrections must be new ledger/audit records.
4. **Every mutating action writes an audit event:** Explicit `audit()` service call (`core.AuditEvent`). Signals are avoided for auditing.
5. **Statutory rates, curriculum structures, and export schemas are data, not code:** Configured via tables/fixtures.
6. **Every ingest endpoint is idempotent:** Handle retries safely using `Idempotency-Key` and `core.IdempotencyRecord`.
7. **Offline is a first-class state:** For gates, POS, and teacher mobile apps, design sync and queues before the UI.
8. **No PII in logs, analytics, error reports, or SMS bodies:** Enforce data masking and encrypt sensitive data at rest (AES-256).
9. **Fail closed on permissions:** Undeclared handler permissions cause build/test failure.
10. **`id-ID` first:** Indonesian is the primary language and source; English is secondary translation.
11. **Zero Unscoped or Destructive Operations:** Never perform any `DELETE`, `TRUNCATE`, `DROP`, or bulk destructive command without an explicit `WHERE` clause and explicit user confirmation.
12. **Mandatory 5-Stage SOP:** Strictly adhere to **Plan -> Dev -> Test -> PR (fetch latest main & ensure mergeable via Pre-PR Synchronization & Conflict Resolution Protocol) -> Merge automatically once CI has passed -> Wait for the deploy instruction**. Merging follows `AGENTS.md` §4 stage 5 (no separate "merge N" instruction needed once CI is green); never deploy without an explicit user instruction.

---

## 5. Async Tasks, Concurrency & Locking Architecture

From `spec/01 §5`:

1. **MySQL Named Advisory Locks (`ARC-007`):**
   - Every scheduled command MUST acquire `GET_LOCK('educore:<job>', 0)` via `core.locks.advisory_lock` and exit cleanly if the lock is held.
   - Overlapping runs are forbidden across all app hosts.
2. **Database-Backed Task Queue (`ARC-010`, `ARC-011`):**
   - Asynchronous jobs are stored in `core.TaskQueue` (`run_after`, `attempts`, `status`).
   - Drained every minute by `drain_tasks --limit 200` using `SELECT ... FOR UPDATE SKIP LOCKED` for lock-free multi-server claiming.
3. **Exponential Backoff Retries (`ARC-012`):**
   - Retry intervals on failure: 1 min, 5 min, 15 min, 60 min.
   - Transitions to `DEAD_LETTER` upon exceeding `max_attempts`.
4. **Scheduled Job Logging (`ARC-008`):**
   - Every scheduled cron command writes an execution row to `core.JobRun` tracking `job_name`, `started_at`, `finished_at`, `status`, `items_processed`, and `error_text`.
5. **Row Lock Order for Money Paths (wallet, then sale, then dispute):**
   - One order everywhere, or two paths deadlock (InnoDB 1213 = a 500 on a charge): `POSQRSession` row (session-token charge only) -> `Wallet` row -> `POSTransaction` (sale) row -> `QRDispute` row(s) of that sale. Settlement is separate: `Merchant` row -> `MerchantSettlementAdjustment` rows. `mark_settlement_paid` takes the `Merchant` row too. Never lock a sale before its wallet, and never a dispute before its sale (`void_pos_transaction`, `open_qr_dispute`, `resolve_qr_dispute` and `charge_qr_session` all follow this).
   - The dispute is last, and `resolve_qr_dispute` reads it unlocked first only to find the wallet. It cannot be first: `void_pos_transaction` closes the sale's open dispute, and a locking read of a sale's disputes before the wallet, on a sale that has none yet, takes a gap lock that blocks a concurrent `open_qr_dispute` insert which holds the wallet the void is waiting for.
   - Inserting a child row (sale, adjustment, wallet transaction) takes a shared lock on its foreign-key parent (`Merchant`, `Wallet`). That is what queues a settlement run behind an in-flight dispute resolution instead of crossing it.
   - A locking read (`select_for_update`) on `pos_transactions` by a non-unique index can take gap locks that collide with another transaction's row lock. It is safe only after the wallet lock is held, because every writer of that wallet queues on it first.
   - Under REPEATABLE READ a plain read after waiting for a lock still shows the transaction's older snapshot. Re-check "already done?" (idempotency key, dispute status, settlement status) with a locking read once the lock is held. `record_wallet_transaction` and `charge_qr_session` do this.
   - A unique key that includes a nullable column does not bind rows where it is NULL (a decal charge has no terminal), so idempotency for those rests on the wallet lock and the re-check above, not on the constraint.
   - Every new money path adds a real-thread round test to `apps/wallet/tests/test_mysql_money_concurrency.py` (MySQL only; `_run_together` helper).

---

## 6. Environment & Platform Conventions

1. **Database Backend:**
   - Production/Staging: MySQL 8.0 (`utf8mb4_0900_ai_ci`).
   - Development & Testing: MySQL 8.0 instance configured via `.env` (`EDUCORE_DB_*` and `EDUCORE_TEST_DB_NAME=test_educore`). Pure-Python driver fallback via `pymysql.install_as_MySQLdb()`.
   - Lightweight test fallback: `EDUCORE_USE_SQLITE=1` supported for running fast offline test suites.
2. **Test Runner:**
   - Tests run via `python manage.py test` or `pytest` (`--reuse-db` enabled by default in `pytest.ini` for remote/local MySQL speed).
   - Dynamic test models in tests use portable raw SQL creation to ensure cross-engine compatibility.

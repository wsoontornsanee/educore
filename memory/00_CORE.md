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
- **Layer 2 (Manager Layer):** `TenantModel.objects` uses `TenantManager`, filtering automatically by the current thread-local `foundation_id` set by `TenancyMiddleware`. Unscoped access is permitted only via `TenantModel.all_tenants` (restricted to internal ops/admin scripts).
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
   - The API always returns `{"amount": "1500000.00", "currency": "IDR"}` as a string, never a formatted value or float.
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

# 15 — Reporting, Analytics and Metering

## 1. Scope

The read side: operational reports, the foundation dashboard's data source,
subscription metering that drives billing, and internal product analytics.

## 2. Architecture

MySQL has no materialised views, so the reporting layer is **plain `rpt_*` tables
in the same database**, rebuilt by the `refresh_reporting` management command:
`--scope=dashboard` every 5 minutes (incremental, last 2 days) and `--scope=full`
nightly. Reporting is a read-only Django app: it owns the `rpt_*` tables and reads
other apps only through their services during a refresh.

- The transactional MySQL tables remain the write model. No separate warehouse until >50 schools.
- Refresh cadence: 5 minutes for dashboards, nightly for heavy aggregates.
- All report queries MUST hit the `rpt_*` tables, never transactional tables directly.
- Every `rpt_*` table carrying money MUST carry its `currency`; cross-currency roll-ups convert only at the foundation level using `fx_rates` (spec 16 §5) and MUST record `fx_rate_date`.

```
rpt_daily_attendance(school_id, date, class_id, present, late, sick, permitted, absent, rate_pct)
rpt_daily_finance(school_id, date, currency, billed, collected, outstanding, payments_count, fees)
rpt_ar_aging(school_id, student_id, as_of, bucket, currency, amount)
rpt_wallet_activity(school_id, date, currency, topups, purchases, commission, active_wallets)
rpt_academic_performance(school_id, term_id, class_id, subject_id, avg_score, band_distribution JSON)
rpt_active_students(foundation_id, school_id, month, active_count, computed_at)   -- billing source
```

## 3. Operational reports (must ship)

| Report | Audience | Filters | Formats |
|---|---|---|---|
| Daily attendance summary | school_admin, teacher | date, class, status | screen, XLSX, PDF |
| Chronic absence watchlist | school_admin, counsellor | term, threshold % | screen, XLSX |
| Late-arrival trend | school_admin | date range, class | screen |
| Collection performance | finance_officer, foundation_admin | period, school | screen, XLSX, PDF |
| AR aging | finance_officer, foundation_admin | as-of date, school | screen, XLSX |
| Arrears by student | finance_officer | bucket, class | screen, XLSX |
| Payment method mix | foundation_admin | period | screen |
| Canteen sales & commission | foundation_admin, merchant | period, merchant | screen, XLSX |
| Student nutrition summary | parent, school_admin | date range | screen |
| Grade distribution | school_admin, teacher | term, subject, class | screen |
| Report-card completion | school_admin | term | screen |
| Behaviour incidents | school_admin, counsellor | term, category | screen, XLSX |
| Clinic visits | clinic_officer, school_admin | date range | screen, XLSX |
| Payroll cost summary | foundation_admin | period | screen, XLSX |
| Device uptime | school_admin, support | date range | screen |

| ID | Requirement |
|---|---|
| `RPT-001` | Every report MUST respect the requester's role scope (02 §4) — a report is not a permission bypass. |
| `RPT-002` | Exports over 10,000 rows MUST run as async jobs with a notification and a 24h-expiring download link. |
| `RPT-003` | Every export MUST carry a header: report name, filters, generated-at, generated-by. |
| `RPT-004` | Any export containing PII MUST be watermarked and audited (`CMP-016`). |
| `RPT-005` | Reports MUST show their data freshness timestamp. |
| `RPT-005b` | An export or refresh that would exceed a cron slot MUST batch with `--limit` and a stored cursor (`ARC-009`). |
| `RPT-006` | Attendance and finance reports MUST reconcile exactly with the underlying records — a discrepancy check job MUST run nightly and alert on drift. |

## 4. Subscription metering

| ID | Requirement |
|---|---|
| `RPT-007` | "Active student" for billing = a student with status `ACTIVE` on the **last day of the month**, at any point enrolled in a class for that month. The definition MUST be implemented once, in one function. |
| `RPT-008` | `rpt_active_students` MUST be immutable once the month closes — it is the invoice basis for EduCore's own billing. |
| `RPT-009` | A foundation-visible metering statement MUST show the counted students per school so disputes are resolvable from the UI. |
| `RPT-010` | Module-tier pricing MUST be computed from entitlements active during that month, prorated by days if changed mid-month. |
| `RPT-011` | Payment-processing and take-rate revenue MUST be reportable per foundation per month alongside subscription revenue. |

## 5. Internal product analytics

| ID | Requirement |
|---|---|
| `RPT-012` | North-star metric: **weekly active parent accounts as % of enrolled students**. Computed per school, trended. |
| `RPT-013` | Health metrics per school: collection rate, parent app WAU%, teacher attendance-submission compliance %, gate uptime %, canteen adoption %. |
| `RPT-014` | An at-risk-account view MUST flag schools whose health metrics decline for 3 consecutive weeks — renewal risk. |
| `RPT-015` | Analytics events MUST NOT contain PII; identifiers are hashed tenant-scoped ids. |
| `RPT-016` | Time-to-value tracking per new school: days from contract to first gate scan, first invoice issued, first parent login, 50% parent activation. |

## 6. API

```
GET  /reports/attendance/daily?school_id&date&class_id
GET  /reports/attendance/chronic?term_id&threshold
GET  /reports/finance/collection?school_id&from&to
GET  /reports/finance/ar-aging?school_id&as_of
GET  /reports/wallet/sales?merchant_id&from&to
GET  /reports/academic/distribution?term_id&subject_id
GET  /reports/payroll/summary?school_id&period
GET  /reports/devices/uptime?school_id&from&to
POST /reports/:key/export          {filters, format} -> {job_id}
GET  /exports/:job_id
GET  /metering/statements?foundation_id&month
GET  /internal/health-metrics?foundation_id      (platform role only)
```

## 7. Acceptance criteria

1. Daily attendance report totals equal a direct count of `attendance_days` for the same date and school.
2. An 80,000-row AR export completes asynchronously and its link 404s after 24 hours.
3. A teacher requesting the collection-performance report receives 403.
4. Closing October freezes the October active-student count; a November backdated enrolment does not change it.
5. The at-risk view flags a school whose parent WAU% fell three weeks running.

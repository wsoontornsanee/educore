# 03 — Foundation Portal (Yayasan)

## 1. Scope

The multi-campus governance surface sold to the Ketua Yayasan. It is the reason
the deal closes: consolidated money, consolidated enrolment, and an audit trail.
It owns **no operational data** — it is a read/approve layer over other modules.

## 2. Screens

| Screen | Purpose | Key data |
|---|---|---|
| Consolidated dashboard | Foundation health at a glance | Collection rate, arrears, headcount, attendance rate, canteen volume |
| Campus comparison | Rank/compare schools | Per-school KPI table, sortable, exportable |
| Financial consolidation | Cross-campus P&L view | Revenue by fee type, outstanding AR aging, payroll cost |
| Enrolment pipeline | Admissions + retention | Prospects, accepted, active, churned by campus and grade |
| Approvals inbox | Governance gates | Discount/waiver approvals, refund approvals, payroll run approvals |
| Audit explorer | Compliance | Filterable `audit_events` with export |
| Foundation settings | Config | Fee catalogue defaults, approval thresholds, branding, entitlements |

## 3. Requirements

| ID | Requirement |
|---|---|
| `FND-001` | Dashboard MUST show, for a selected period and campus set: total billed, total collected, collection rate %, outstanding AR, AR aging buckets (0–30/31–60/61–90/90+ days), active students, average daily attendance %, cashless campus spend. |
| `FND-002` | Every KPI MUST be drillable to the underlying record list in ≤2 clicks. |
| `FND-003` | Period selector: current month, term-to-date, year-to-date, custom range. Comparison against prior period MUST be shown as absolute + percentage delta. |
| `FND-004` | Campus comparison MUST support a foundation with up to 25 schools without pagination lag (server-side aggregate, not client rollup). |
| `FND-005` | Numbers on this screen MUST be sourced from the reporting rollup tables (see 15), never from live transactional joins. |
| `FND-005b` | A foundation whose schools bill in more than one currency MUST see consolidated figures in the foundation's `reporting_currency`, labelled with that currency and the FX rate date used (see 16 §5). Per-school figures MUST remain in the school's own currency. |
| `FND-006` | Read-model freshness MUST be displayed ("Updated 4 minutes ago") and MUST be ≤15 minutes stale. |
| `FND-007` | Approvals inbox MUST implement a threshold policy: any discount, waiver or refund above `foundation.approval_threshold` requires foundation approval before it takes effect. |
| `FND-008` | An approval decision MUST capture approver, timestamp, and a mandatory free-text reason; rejections MUST notify the requester. |
| `FND-009` | Approvals MUST be actionable from the parent-facing surfaces' inverse: nothing changes state until approved — requests sit in `PENDING_APPROVAL`. |
| `FND-010` | Audit explorer MUST filter by actor, school, module, action, entity, date range, and export to CSV (max 100k rows, async job + download link). |
| `FND-011` | Foundation admin MUST be able to define a foundation-level **fee catalogue template** that new schools inherit and may override only where `overridable = true`. |
| `FND-012` | Foundation admin MUST be able to toggle module entitlements per school within plan limits (see 02 §6). |
| `FND-013` | Branding: logo, primary colour, school letterhead; applied to invoices, report cards and parent app header. Defaults MUST be the EduCore white/red identity. |
| `FND-014` | All dashboard exports (PDF/XLSX) MUST include generation timestamp, actor name and applied filters in the header. |

## 4. Data contract (read model)

```sql
-- refreshed by cron every 5 minutes
rpt_foundation_kpis(   -- plain MySQL table, rebuilt by `refresh_reporting` cron
  foundation_id, school_id, period_start, period_end,
  billed, collected, outstanding,
  ar_0_30, ar_31_60, ar_61_90, ar_90_plus,
  active_students, avg_attendance_pct, campus_spend,
  currency, reporting_currency, fx_rate_date,
  computed_at
)
```

## 5. API

```
GET  /foundation/kpis?from&to&school_ids[]&compare=prev_period
GET  /foundation/schools/compare?from&to&metric=collection_rate
GET  /foundation/ar-aging?as_of&school_ids[]
GET  /foundation/enrolment?from&to&group_by=grade|campus
GET  /foundation/approvals?status=pending&type=discount|waiver|refund|payroll
POST /foundation/approvals/:id/decide   {decision: APPROVE|REJECT, reason}
GET  /foundation/audit?actor&module&action&from&to  (cursor)
POST /foundation/exports                {report, format, filters} -> {job_id}
GET  /foundation/exports/:job_id        -> {status, download_url?}
GET  /foundation/settings | PATCH /foundation/settings
```

## 6. Acceptance criteria

1. A foundation with 12 schools and 9,000 students renders the dashboard in < 2s (p95).
2. Approving a Rp 5,000,000 waiver marks the underlying invoice line `WAIVED` and emits `finance.waiver.approved`.
3. Rejecting a refund returns the request to the finance officer with the rejection reason visible.
4. Audit CSV export of 60k rows completes asynchronously and the link expires after 24h.
5. Disabling `payroll` for one school hides payroll navigation for that school's admins only.

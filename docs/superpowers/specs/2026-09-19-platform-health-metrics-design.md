# Platform health metrics, slice 1: parent WAU, at-risk flag, internal endpoint

Notion: "Platform health metrics missing: parent WAU%, collection rate, at-risk schools, time-to-value (RPT-012..016)".
Spec: `spec/15-reporting-and-analytics.md` RPT-012, RPT-014, RPT-015, `GET /internal/health-metrics`.

## Scope

Slice 1 delivers RPT-012 (weekly active parent accounts as % of enrolled students, per school, trended), RPT-014 (flag schools whose WAU% fell three consecutive weeks) and the platform-only `GET /internal/health-metrics` endpoint.

Out of scope, logged as Notion Todos: the other RPT-013 metrics (collection rate, attendance-submission compliance, gate uptime, canteen adoption) as extra columns on the same rollup; RPT-016 time-to-value.

## 1. Activity signal

Nothing records that a user was active: login is not audited and JWT refresh is stateless.

- `identity.User.last_activity_date` (`DateField`, null): the gate.
- `identity.UserActivityDay` (`foundation_id`, `user_id`, `date`; unique on the three): the history WAU is computed from. Ids and dates only, no PII (RPT-015). A `TenantModel` with the soft-delete uniqueness marker like the other tenant tables (append-only in practice).
- `EduCoreJWTAuthentication.authenticate` already loads the user on each request. After the tenant context is set, if `user.last_activity_date != today` (Asia/Jakarta calendar date) it runs `User.all_tenants.filter(pk=user.pk).exclude(last_activity_date=today).update(last_activity_date=today)`. Only a request whose update matches a row (`== 1`) inserts the `UserActivityDay` row, so concurrent first requests of a day insert once. Cost: no extra read; at most two writes per user per day.
- Recording failure must never fail the request: wrapped so an exception is logged and swallowed.
- Recorded for every authenticated user (parents, staff); WAU filters to parents at read time.

## 2. Weekly rollup

`reporting.RptParentWeeklyActivity(TenantModel)`: `school`, `week_start` (Monday), `active_parents`, `enrolled_students`, `computed_at`; unique per (`foundation_id`, `school`, `week_start`, marker) and indexed `foundation_id`-first, like `RptActiveStudent`.

- `active_parents` = distinct users with a `UserActivityDay` in `[week_start, week_start+6]` who hold an active `GuardianLink` to an active student of that school.
- `enrolled_students` = students with status `ACTIVE` at that school at refresh time.
- WAU% = `active_parents / enrolled_students` (spec wording: accounts over students, so it can exceed 100% when two parents share a child; raw counts are stored so it can be re-derived). A school with 0 enrolled students has no percentage.
- New refresher `refresh_parent_weekly_activity(scope)` registered in `REFRESHERS`. `dashboard` (every 5 minutes) does nothing; the nightly `full` run refreshes the current week and backfills the previous seven. A week is frozen once it has ended and has been computed after its end (mirrors RPT-008: past student status cannot be reconstructed, so the denominator must be captured at the time). `full` also backfills missing past weeks from `UserActivityDay`, using the current denominator, only when no row exists.

## 3. At-risk flag (RPT-014)

Computed at read time, not stored. Take the last four **completed** weeks of a school's rollup; flagged when WAU% strictly decreased three times in a row (`p1 < p0`, `p2 < p1`, `p3 < p2`, oldest first). A tie breaks the streak; fewer than four completed weeks, or a week without a percentage, is not flagged; the in-progress week is ignored.

## 4. Endpoint

`GET /api/v1/internal/health-metrics/?foundation_id=<id>` (`foundation_id` optional).

- New platform permission `platform.health.read` in `PLATFORM_ROLE_PERMISSIONS` for `platform_operator`; the view declares `required_permission` and uses `HasRequiredPermission`, so it fails closed. Foundation staff and guardians get 403; anonymous get 401.
- Reads across foundations with `all_tenants` (platform role has no tenant context); not a tenant-derived viewset, so the cross-tenant-404 harness does not apply. Documented in the view.
- Response per school: `foundation_id`, `school_id`, `school_name`, `latest_wau_pct` (last completed week), `at_risk`, `weeks`: last 8 completed weeks plus the current, each `{week_start, active_parents, enrolled_students, wau_pct, complete}`.
- Read-only; no audit event needed (no mutation).

## 5. Tests

- Activity recording: first request of the day inserts one row and sets the gate; second request the same day writes nothing; a stale in-memory copy of the user (two requests that both loaded it before either recorded) inserts once; a recording error does not fail the request.
- Rollup: only parents with an active link count; staff activity does not; inactive students and unlinked users excluded; week boundaries (Sunday/Monday); frozen week not recomputed; denominator 0.
- At-risk: three strict drops flagged; tie, two drops, three weeks of data, and an in-progress week each not flagged.
- Endpoint: operator 200, foundation admin 403, anonymous 401; `foundation_id` filter; shape.
- `manage.py check --database default` clean on MySQL (no conditional constraints); every new tenant table passes the foundation-led index test.

## Risks

- A week's `enrolled_students` is the value at freeze time, not a historical truth (repo keeps no status history).
- Hooking `authenticate` touches every authenticated API request; the gate makes the steady-state cost one date comparison on an already-loaded user.
- No platform operator exists on PRD (separate Notion item), so the endpoint has no caller there until one is assigned.

# Slice 2: collection rate, attendance-submission compliance, gate uptime, canteen adoption (RPT-013)

Notion: "Platform health metrics slice 2 ... (RPT-013)". Definitions below are decisions (additive, reversible; the item said "define"). Parent WAU% (the fifth RPT-013 metric) is slice 1.

## Storage

The metrics are extra columns on `rpt_parent_weekly_activity` (one row per school per week), each a numerator and a denominator so the percentage is derived at read time and a definition tweak needs no rewrite of stored ratios. All nullable: null means "not computed yet".

| Metric | Columns | Definition |
|---|---|---|
| Collection rate | `collection_billed`, `collection_collected` | IDR invoices whose `due_date` is in the week (status ISSUED, PARTIALLY_PAID, PAID or WRITTEN_OFF; drafts and cancelled are not receivables) vs the sum of `PaymentAllocation` on them from SETTLED payments settled on or before the week's last day. An on-time collection rate: a payment after the week never rewrites it, so the week is stable and can freeze. Other currencies are excluded (a ratio across currencies is meaningless). |
| Attendance-submission compliance | `attendance_expected_periods`, `attendance_submitted_periods` | Expected = `TimetableSlot` periods of the school on each day of the week that is before today, inside the class subject's term and not excused by an `affects_attendance` calendar event (`attendance.services.find_calendar_exemption`, the same rule the teacher agenda uses). Submitted = those (slot, date) pairs with any `PeriodAttendance` row, whoever submitted. Sundays and holidays fall out because no slot or an exempting event covers them. |
| Gate uptime | `gate_samples`, `gate_up_samples` | Sum of `hardware.DeviceUptimeDay` over the week. New: `check_device_health` (every 10 min) ends with `record_gate_uptime_sample`, one sample per GATE_READER/FACE_TERMINAL device that is not RETIRED, only inside the school's operational hours (06:00-18:00 local, not Sunday); up = ONLINE or DEGRADED after the stale sweep. The repo only keeps the latest heartbeat per device, so history has to be sampled; a school with no gate devices has no percentage. |
| Canteen adoption | `canteen_active_students` (denominator `enrolled_students`) | Distinct counted students with a COMPLETED `POSTransaction` at a CANTEEN merchant of the school in the week, over the row's `enrolled_students` (the same RPT-007 population as WAU). |

## Refresh and freezing

Same nightly `full` refresher (`refresh_parent_weekly_activity`), same eight-week window. The row now has two independently frozen parts: WAU (`computed_at`) and health (`health_computed_at`); each freezes once computed after the week ended. Reason for freezing the health part: POS sales are archived after 400 days and uptime samples are day counters, so a late recompute must not overwrite the week with less. Weeks written by slice 1 have a null `health_computed_at` and get the health part on the first run after deploy (WAU part untouched); their attendance/collection/canteen values are rebuilt from source data, their gate uptime is empty because sampling starts at deploy.

## Endpoint and RPT-014

`GET /internal/health-metrics/` weeks gain `collection_rate_pct`, `attendance_compliance_pct`, `gate_uptime_pct`, `canteen_adoption_pct` (null when the week has no data or a zero base). RPT-014 applies to **every** metric, each judged on its own by the slice-1 rule (four completed weeks, three strict drops). A school is `at_risk` when any metric is declining, and the new `declining_metrics` array names which (`wau_pct`, `collection_rate_pct`, ...). A metric with a missing week or no data never flags.

## Risks

- Collection and attendance are rebuilt from today's data for weeks that predate the columns (invoices, timetable and calendar are edited in place); acceptable, they are only ever computed once per week.
- Uptime of the first weeks after deploy is partial (sampling begins at deploy); a school appears at 100% if its only samples are healthy ones.
- Periods for a school with timetables but no teacher using the feature score 0%, by design: that is the at-risk signal.

# Slice 3: time-to-value per new school (RPT-016)

Notion: "Platform health metrics slice 3: time-to-value per new school (RPT-016)".

## Contract date

`identity.Foundation.contract_date` (`DateField`, null). The contract is signed with the Yayasan, and `Foundation` is not a tenant table and is not on any tenant-facing form or serializer, so a school cannot edit the start of its own clock. The platform sets it in the Django admin (`FoundationAdmin`). Nothing is backfilled: until it is set, a school's milestone dates show and their `days` are `null`. Every school of a foundation is measured from that one date, so a school added later under the same contract shows large numbers (a per-school override is a follow-up if that matters).

## Milestones

Derived at read time in `apps/reporting/health.py` (no new table, no cron job), added to each school in `GET /internal/health-metrics/` as `time_to_value`:

```
"time_to_value": {
  "contract_date": "2026-08-01" | null,
  "first_gate_scan":      {"date": "2026-08-09" | null, "days": 8 | null},
  "first_invoice":        {...},
  "first_parent_login":   {...},
  "parent_activation_50": {...}
}
```

- `first_gate_scan`: earliest `GateEvent` with status `ACCEPTED` for the school, as a calendar day in the server timezone.
- `first_invoice`: earliest `Invoice.issue_date` for the school, ignoring `DRAFT` (a cancelled invoice was still issued).
- `first_parent_login`: earliest `UserActivityDay` of any parent account (guardian with a login) linked to an active, enrolled student of the school.
- `parent_activation_50`: the day the number of such parent accounts that have ever been active reached `ceil(50% x enrolled students)`. Accounts over students, the same ratio as the WAU north star; a shared parent counts once.
- `days` = milestone date minus `contract_date`; negative when the milestone came before the contract date (a pilot).

## Known limits

- `UserActivityDay` exists only from slice 1's deploy, so a school already live then shows first login and activation no earlier than that.
- The parent set and the denominator are the school's current roster; past rosters are not kept (same simplification as RPT-008 and slice 1).
- The endpoint runs a handful of grouped queries per school; fine for an internal, platform-only page, and the four milestones can move into the nightly rollup if the school count makes it slow.

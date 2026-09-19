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
- New refresher `refresh_parent_weekly_activity(scope)` registered in `REFRESHERS`. `dashboard` recomputes the current week. A week is frozen once it has ended and has been computed after its end (mirrors RPT-008: past student status cannot be reconstructed, so the denominator must be captured at the time). `full` also backfills missing past weeks from `UserActivityDay`, using the current denominator, only when no row exists.

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

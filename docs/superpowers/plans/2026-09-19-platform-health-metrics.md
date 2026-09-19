# Platform health metrics slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record parent activity, roll it up into weekly WAU% per school, flag three-week declines, and expose both to the platform role at `GET /api/v1/internal/health-metrics/`.

**Architecture:** `EduCoreJWTAuthentication` records at most one `UserActivityDay` row per user per day, gated by an atomic update of `User.last_activity_date`. A new `refresh_reporting` refresher rolls those rows into `RptParentWeeklyActivity`. A read-time service derives WAU% and the at-risk flag; a platform-permission-gated view serves it.

**Tech Stack:** Django 5.1, DRF, simplejwt, MySQL 8 (SQLite for fast local tests). Backend only.

Spec: `docs/superpowers/specs/2026-09-19-platform-health-metrics-design.md`.

## Global Constraints

- MySQL 8 only, cron-driven refresh via existing `refresh_reporting`; no Redis/Celery/new infrastructure.
- Every tenant model inherits `core.models.TenantModel` and has a `foundation_id`-led composite index (`apps/core/tests/test_tenant_indexes.py`).
- Soft-delete unique constraints use `soft_delete_uniqueness_marker()`, never a conditional `UniqueConstraint` (MySQL W036).
- No PII in the new tables or the endpoint response: ids, dates and counts only (RPT-015). The response carries `school_name`, which is not personal data.
- Handlers declare `required_permission` and use `HasRequiredPermission` (fail closed, IAM-010).
- Recording activity must never fail or slow a request beyond one date comparison in the steady state; any recording error is logged and swallowed.
- Calendar dates are Asia/Jakarta (`timezone.localdate()`); weeks start Monday.
- Run tests with `EDUCORE_USE_SQLITE=1 python manage.py test <label>`; do not run the full suite locally (AGENTS.md), CI does.
- Commits end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

## File Structure

- Modify `apps/identity/models.py`: `User.last_activity_date`; new `UserActivityDay`.
- Create `apps/identity/activity.py`: `record_activity(user)`.
- Modify `apps/identity/authentication.py`: call `record_activity`.
- Create `apps/identity/tests/test_user_activity.py`.
- Modify `apps/reporting/models.py`: `RptParentWeeklyActivity`.
- Modify `apps/reporting/services.py`: extract RPT-007 helpers, add `refresh_parent_weekly_activity`.
- Modify `apps/reporting/management/commands/refresh_reporting.py`: register the refresher.
- Create `apps/reporting/tests/test_parent_weekly_activity.py`.
- Create `apps/reporting/health.py`: `is_declining`, `get_health_metrics`.
- Create `apps/reporting/tests/test_health_metrics.py` (service) and `apps/reporting/tests/test_health_metrics_endpoint.py` (API).
- Modify `apps/reporting/views.py`, create `apps/reporting/internal_urls.py`, modify `educore/urls.py`.
- Modify `apps/identity/rbac.py`: `platform.health.read`.
- Modify `memory/01_PROJECT.md`, and the spec's test line (Task 1).
- Migrations generated with `makemigrations`.

---

### Task 1: Activity signal (identity)

**Files:**
- Modify: `apps/identity/models.py` (User fields near line 258; append `UserActivityDay` at end of file)
- Create: `apps/identity/activity.py`
- Modify: `apps/identity/authentication.py` (before the final `return result`)
- Create: `apps/identity/tests/test_user_activity.py`
- Modify: `docs/superpowers/specs/2026-09-19-platform-health-metrics-design.md` (tests bullet)

**Interfaces:**
- Produces: `apps.identity.models.UserActivityDay(foundation_id, user, date)`; `User.last_activity_date: date | None`; `apps.identity.activity.record_activity(user) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `apps/identity/tests/test_user_activity.py`:

```python
"""Activity signal for platform health metrics (spec docs/superpowers/specs/2026-09-19-platform-health-metrics-design.md §1)."""
import datetime
from types import SimpleNamespace
from unittest import mock

from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.identity.activity import record_activity
from apps.identity.models import Foundation, User, UserActivityDay


class RecordActivityTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Yayasan Aktif', brand_name='Aktif')
        self.user = User.all_tenants.create_user(
            phone_e164='+6281200000501', full_name='Ibu Aktif', foundation_id=self.foundation.id,
        )

    def _days(self):
        return UserActivityDay.all_tenants.filter(user_id=self.user.pk)

    def test_first_call_of_the_day_sets_the_gate_and_inserts_one_row(self):
        record_activity(self.user)
        today = timezone.localdate()
        self.assertEqual(self.user.last_activity_date, today)
        self.assertEqual(User.all_tenants.get(pk=self.user.pk).last_activity_date, today)
        self.assertEqual(list(self._days().values_list('date', flat=True)), [today])
        self.assertEqual(self._days().get().foundation_id, self.foundation.id)

    def test_second_call_the_same_day_does_no_query(self):
        record_activity(self.user)
        with self.assertNumQueries(0):
            record_activity(self.user)
        self.assertEqual(self._days().count(), 1)

    def test_stale_copy_of_the_user_does_not_insert_twice(self):
        # Two requests loaded the user before either recorded: only the one whose UPDATE matches inserts.
        first = User.all_tenants.get(pk=self.user.pk)
        second = User.all_tenants.get(pk=self.user.pk)
        record_activity(first)
        record_activity(second)
        self.assertEqual(self._days().count(), 1)

    def test_a_new_day_inserts_a_new_row(self):
        record_activity(self.user)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        with mock.patch('apps.identity.activity.timezone.localdate', return_value=tomorrow):
            record_activity(self.user)
        self.assertEqual(self._days().count(), 2)

    def test_user_without_a_foundation_is_skipped(self):
        record_activity(SimpleNamespace(foundation_id=None, last_activity_date=None, pk=1))
        self.assertEqual(UserActivityDay.all_tenants.count(), 0)

    def test_a_recording_failure_is_swallowed_and_leaves_the_gate_open(self):
        broken = mock.MagicMock()
        broken.all_tenants.create.side_effect = DatabaseError('boom')
        with mock.patch('apps.identity.activity.UserActivityDay', broken):
            record_activity(self.user)  # must not raise
        self.assertIsNone(User.all_tenants.get(pk=self.user.pk).last_activity_date)
        record_activity(User.all_tenants.get(pk=self.user.pk))  # retry succeeds
        self.assertEqual(self._days().count(), 1)


class AuthenticationHookTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='Yayasan Hook', brand_name='Hook')
        self.user = User.all_tenants.create_user(
            phone_e164='+6281200000502', full_name='Bapak Hook', foundation_id=self.foundation.id,
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_authenticated_request_records_one_row_per_day(self):
        self.assertEqual(self.client.get('/api/v1/me').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/me').status_code, 200)
        self.assertEqual(UserActivityDay.all_tenants.filter(user_id=self.user.pk).count(), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_user_activity -v 2`
Expected: ImportError (`apps.identity.activity` / `UserActivityDay` not defined).

- [ ] **Step 3: Add the model fields**

In `apps/identity/models.py`, in `class User`, after `locked_until` (line ~254), add:

```python
    last_activity_date = models.DateField(
        null=True, blank=True,
        help_text="Last calendar day (Asia/Jakarta) with an authenticated API request; gate for UserActivityDay.",
    )
```

Append at the end of the file:

```python
class UserActivityDay(TenantModel):
    """One row per user per calendar day (Asia/Jakarta) with an authenticated API request.

    The history behind weekly-active-parent metrics (RPT-012). Written by
    `apps.identity.activity.record_activity`; ids and dates only, no PII (RPT-015).
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_days')
    date = models.DateField()
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'user_activity_days'
        indexes = [
            models.Index(fields=['foundation_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'user', 'date', 'active_uniq_marker'],
                name='unique_user_activity_day',
            ),
        ]

    def __str__(self):
        return f"user={self.user_id} {self.date}"
```

- [ ] **Step 4: Create the recorder**

Create `apps/identity/activity.py`:

```python
"""Records that a user was active today (RPT-012 input).

Called from `EduCoreJWTAuthentication.authenticate`, which has already loaded the
user, so the steady-state cost of a request is one date comparison. The first
request of a day claims the day with an atomic UPDATE; only the request whose
UPDATE matched a row inserts the `UserActivityDay`, so racing first requests
insert exactly once.
"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.identity.models import User, UserActivityDay

logger = logging.getLogger(__name__)


def record_activity(user) -> None:
    foundation_id = getattr(user, 'foundation_id', None)
    if not foundation_id:
        return
    today = timezone.localdate()
    if user.last_activity_date == today:
        return
    try:
        with transaction.atomic():
            claimed = (
                User.all_tenants.filter(pk=user.pk)
                .exclude(last_activity_date=today)
                .update(last_activity_date=today)
            )
            if claimed:
                UserActivityDay.all_tenants.create(foundation_id=foundation_id, user_id=user.pk, date=today)
        user.last_activity_date = today
    except Exception:
        logger.exception("Could not record user activity")
```

- [ ] **Step 5: Hook it into authentication**

In `apps/identity/authentication.py` add `from apps.identity.activity import record_activity` to the imports, and immediately before the final `return result` of `authenticate`:

```python
        record_activity(user)

        return result
```

- [ ] **Step 6: Generate the migration and run tests**

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations identity -n user_activity_day`
Expected: one new migration adding `User.last_activity_date` and `UserActivityDay`.

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.identity.tests.test_user_activity apps.core.tests.test_tenant_indexes apps.identity.tests.test_jwt_auth -v 1`
Expected: PASS.

- [ ] **Step 7: Align the spec's test bullet**

In the spec's "## 5. Tests" first bullet, replace "two racing first requests insert once (MySQL two-thread test, like #276)" with "a stale in-memory copy of the user (two requests that both loaded it before either recorded) inserts once".

- [ ] **Step 8: Commit**

```bash
git add apps/identity apps/core docs/superpowers/specs/2026-09-19-platform-health-metrics-design.md
git commit -m "feat(identity): record one activity day per user per day (RPT-012 signal)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Weekly rollup (reporting)

**Files:**
- Modify: `apps/reporting/models.py` (append)
- Modify: `apps/reporting/services.py` (imports, helpers, new refresher)
- Modify: `apps/reporting/management/commands/refresh_reporting.py`
- Create: `apps/reporting/tests/test_parent_weekly_activity.py`

**Interfaces:**
- Consumes: `UserActivityDay`, `GuardianLink`, `Guardian.user` (Task 1 + existing).
- Produces: `RptParentWeeklyActivity(foundation_id, school, week_start, active_parents, enrolled_students, computed_at)`; `services._week_start(day) -> date`; `services.refresh_parent_weekly_activity(scope) -> {'rows_written': int, 'scope': str}`; `services.PARENT_ACTIVITY_BACKFILL_WEEKS = 8`.

- [ ] **Step 1: Write the failing tests**

Create `apps/reporting/tests/test_parent_weekly_activity.py`:

```python
import datetime

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Guardian, GuardianLink, Person, Student, User, UserActivityDay
from apps.reporting.management.commands.refresh_reporting import REFRESHERS
from apps.reporting.models import RptParentWeeklyActivity
from apps.reporting.services import _week_start, refresh_parent_weekly_activity


class WeekStartTests(SimpleTestCase):
    def test_monday_maps_to_itself_and_sunday_to_the_previous_monday(self):
        self.assertEqual(_week_start(datetime.date(2026, 9, 14)), datetime.date(2026, 9, 14))  # Monday
        self.assertEqual(_week_start(datetime.date(2026, 9, 20)), datetime.date(2026, 9, 14))  # Sunday


class RefreshParentWeeklyActivityTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.today = timezone.localdate()
        self.week = _week_start(self.today)
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        self.parent = self._parent('+6281200000601', self.fx['student'])

    def _parent(self, phone, student):
        user = User.all_tenants.create_user(phone_e164=phone, full_name='Wali', foundation_id=self.fid)
        person = Person.all_tenants.create(foundation_id=self.fid, full_name='Wali Murid')
        guardian = Guardian.all_tenants.create(foundation_id=self.fid, person=person, user=user)
        GuardianLink.all_tenants.create(foundation_id=self.fid, guardian=guardian, student=student)
        self.guardian = guardian
        return user

    def _active(self, user, day):
        UserActivityDay.all_tenants.create(foundation_id=self.fid, user_id=user.pk, date=day)

    def _row(self, week=None):
        return RptParentWeeklyActivity.all_tenants.get(
            foundation_id=self.fid, school=self.fx['school'], week_start=week or self.week,
        )

    def test_registered_with_refresh_reporting(self):
        self.assertIn(refresh_parent_weekly_activity, [fn for _name, fn in REFRESHERS])

    def test_counts_a_linked_parent_active_this_week(self):
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (1, 1))

    def test_staff_activity_is_not_counted(self):
        self._active(self.fx['teacher_user'], self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        self.assertEqual(self._row().active_parents, 0)

    def test_parent_of_an_inactive_student_is_not_counted_and_denominator_drops(self):
        self.fx['student'].status = Student.STATUS_INACTIVE
        self.fx['student'].save()
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (0, 0))

    def test_one_parent_of_two_children_counts_once(self):
        person = Person.all_tenants.create(foundation_id=self.fid, nik='3471010101019999', full_name='Adik')
        sibling = Student.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], person=person,
            nisn='5544332299', nis='X-009', status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.fid, student=sibling,
            class_group=self.fx['class_group'], enrolled_at=datetime.date(2026, 7, 1),
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.fid, guardian=self.guardian, student=sibling,
        )
        self._active(self.parent, self.today)
        refresh_parent_weekly_activity(scope='dashboard')
        row = self._row()
        self.assertEqual((row.active_parents, row.enrolled_students), (1, 2))

    def test_last_weeks_activity_does_not_count_this_week(self):
        self._active(self.parent, self.week - datetime.timedelta(days=1))
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row().active_parents, 0)
        self.assertEqual(self._row(self.week - datetime.timedelta(weeks=1)).active_parents, 1)

    def test_dashboard_scope_touches_only_the_current_week(self):
        refresh_parent_weekly_activity(scope='dashboard')
        self.assertEqual(RptParentWeeklyActivity.all_tenants.count(), 1)

    def test_full_scope_backfills_the_last_eight_weeks(self):
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(RptParentWeeklyActivity.all_tenants.count(), 8)

    def test_a_week_computed_after_it_ended_is_frozen(self):
        previous = self.week - datetime.timedelta(weeks=1)
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=previous,
            active_parents=99, enrolled_students=99, computed_at=timezone.now(),
        )
        self._active(self.parent, previous)
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row(previous).active_parents, 99)

    def test_a_week_computed_before_it_ended_is_recomputed_once(self):
        previous = self.week - datetime.timedelta(weeks=1)
        computed_mid_week = timezone.make_aware(datetime.datetime.combine(previous + datetime.timedelta(days=2), datetime.time(12)))
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=previous,
            active_parents=0, enrolled_students=1, computed_at=computed_mid_week,
        )
        self._active(self.parent, previous + datetime.timedelta(days=6))
        refresh_parent_weekly_activity(scope='full')
        self.assertEqual(self._row(previous).active_parents, 1)
        self.assertGreater(timezone.localdate(self._row(previous).computed_at), previous + datetime.timedelta(days=6))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_parent_weekly_activity -v 2`
Expected: ImportError (`RptParentWeeklyActivity`).

- [ ] **Step 3: Add the model**

Append to `apps/reporting/models.py`:

```python
class RptParentWeeklyActivity(TenantModel):
    """Weekly parent-app activity per school (RPT-012 north star: weekly active parent accounts / enrolled students).

    `active_parents` = distinct users with a `UserActivityDay` in the week who hold an active `GuardianLink`
    to a counted student of the school; `enrolled_students` = the RPT-007 active enrolled students at refresh
    time (this repo keeps no status history, so the denominator is whatever it was when the week froze).
    Holds counts only, no PII.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='parent_weekly_activity_reports')
    week_start = models.DateField(help_text=_("Monday of the week (Asia/Jakarta calendar)"))
    active_parents = models.PositiveIntegerField(default=0)
    enrolled_students = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_parent_weekly_activity'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'week_start']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'week_start', 'active_uniq_marker'],
                name='unique_rpt_parent_weekly_activity_per_school_week',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - week {self.week_start}: {self.active_parents}/{self.enrolled_students}"
```

- [ ] **Step 4: Extract the RPT-007 helpers and add the refresher**

In `apps/reporting/services.py`: add `RptParentWeeklyActivity` to the `apps.reporting.models` import (alphabetical, after `RptDailyFinance`), then directly above `refresh_active_students` add:

```python
PARENT_ACTIVITY_BACKFILL_WEEKS = 8


def _active_enrolled_student_ids() -> set:
    """RPT-007, half one: students with a current active ClassEnrollment (implemented once, here)."""
    from apps.academic.models import ClassEnrollment

    return set(
        ClassEnrollment.all_tenants.filter(is_active=True, deleted_at__isnull=True).values_list('student_id', flat=True)
    )


def _counted_student_ids(school, enrolled_ids) -> list:
    """RPT-007, half two: the school's ACTIVE students that are also enrolled, ascending ids."""
    from apps.identity.models import Student

    return sorted(Student.all_tenants.filter(
        foundation_id=school.foundation_id, school=school,
        status=Student.STATUS_ACTIVE, id__in=enrolled_ids,
        deleted_at__isnull=True,
    ).values_list('id', flat=True))


def _week_start(day):
    return day - timedelta(days=day.weekday())
```

Inside `refresh_active_students`, replace the local imports of `ClassEnrollment`/`Student`, the `active_student_ids = set(...)` block and the `counted_ids = sorted(Student.all_tenants...)` block with `active_student_ids = _active_enrolled_student_ids()` and `counted_ids = _counted_student_ids(school, active_student_ids)` (behaviour unchanged; `tests/test_active_students.py` proves it).

Append after `refresh_active_students`:

```python
def refresh_parent_weekly_activity(scope: str) -> dict:
    """RPT-012: rebuild rpt_parent_weekly_activity, one row per school per week.

    scope='dashboard' refreshes only the current week; scope='full' also covers the previous
    PARENT_ACTIVITY_BACKFILL_WEEKS - 1 weeks (backfilling missing ones from UserActivityDay).
    A week whose row was computed after the week ended is frozen (mirrors RPT-008): the denominator
    cannot be reconstructed later, so it is captured once and never rewritten.
    """
    from apps.identity.models import GuardianLink, UserActivityDay

    now = timezone.now()
    current_week = _week_start(timezone.localdate(now))
    weeks = [current_week]
    if scope == 'full':
        weeks += [current_week - timedelta(weeks=n) for n in range(1, PARENT_ACTIVITY_BACKFILL_WEEKS)]

    enrolled_ids = _active_enrolled_student_ids()
    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        counted = _counted_student_ids(school, enrolled_ids)
        parent_user_ids = set(GuardianLink.all_tenants.filter(
            foundation_id=school.foundation_id, student_id__in=counted, deleted_at__isnull=True,
            guardian__deleted_at__isnull=True, guardian__user__isnull=False,
        ).values_list('guardian__user_id', flat=True))

        for week in weeks:
            week_end = week + timedelta(days=6)
            existing = RptParentWeeklyActivity.all_tenants.filter(
                foundation_id=school.foundation_id, school=school, week_start=week,
            ).first()
            if existing and timezone.localdate(existing.computed_at) > week_end:
                continue  # frozen

            active_parents = UserActivityDay.all_tenants.filter(
                foundation_id=school.foundation_id, date__range=(week, week_end), user_id__in=parent_user_ids,
            ).values('user_id').distinct().count()
            RptParentWeeklyActivity.all_tenants.update_or_create(
                foundation_id=school.foundation_id, school=school, week_start=week,
                defaults={'active_parents': active_parents, 'enrolled_students': len(counted), 'computed_at': now},
            )
            rows_written += 1

    return {'rows_written': rows_written, 'scope': scope}
```

- [ ] **Step 5: Register the refresher**

In `refresh_reporting.py` import `refresh_parent_weekly_activity` (alphabetical) and add `('rpt_parent_weekly_activity', refresh_parent_weekly_activity),` to `REFRESHERS` after `rpt_active_students`.

- [ ] **Step 6: Migration and tests**

Run: `EDUCORE_USE_SQLITE=1 python manage.py makemigrations reporting -n rptparentweeklyactivity`
Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_parent_weekly_activity apps.reporting.tests.test_active_students apps.core.tests.test_tenant_indexes apps.core.tests.test_cron_command_smoke -v 1`
Expected: PASS (the cron smoke test runs `refresh_reporting` for both scopes end to end).

- [ ] **Step 7: Commit**

```bash
git add apps/reporting
git commit -m "feat(reporting): weekly parent activity rollup (RPT-012)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Health metrics service and at-risk flag

**Files:**
- Create: `apps/reporting/health.py`
- Create: `apps/reporting/tests/test_health_metrics.py`

**Interfaces:**
- Consumes: `RptParentWeeklyActivity` (Task 2).
- Produces: `health.is_declining(ratios: list[float | None]) -> bool`; `health.get_health_metrics(foundation_id=None, today=None) -> list[dict]` where each dict is `{'foundation_id', 'school_id', 'school_name', 'latest_wau_pct': float | None, 'at_risk': bool, 'weeks': [{'week_start': 'YYYY-MM-DD', 'active_parents', 'enrolled_students', 'wau_pct': float | None, 'complete': bool}]}`.

- [ ] **Step 1: Write the failing tests**

Create `apps/reporting/tests/test_health_metrics.py`:

```python
import datetime

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.reporting.health import get_health_metrics, is_declining
from apps.reporting.models import RptParentWeeklyActivity


class IsDecliningTests(SimpleTestCase):
    def test_three_strict_drops_are_flagged(self):
        self.assertTrue(is_declining([0.8, 0.6, 0.5, 0.4]))

    def test_a_tie_breaks_the_streak(self):
        self.assertFalse(is_declining([0.8, 0.6, 0.6, 0.4]))

    def test_a_rise_breaks_the_streak(self):
        self.assertFalse(is_declining([0.5, 0.6, 0.5, 0.4]))

    def test_only_three_weeks_of_data_is_not_enough(self):
        self.assertFalse(is_declining([0.6, 0.5, 0.4]))

    def test_a_missing_week_is_not_flagged(self):
        self.assertFalse(is_declining([0.8, None, 0.5, 0.4]))

    def test_only_the_last_four_weeks_matter(self):
        self.assertTrue(is_declining([0.1, 0.9, 0.7, 0.5, 0.3]))


class GetHealthMetricsTests(TestCase):
    TODAY = datetime.date(2026, 9, 16)  # a Wednesday; current week starts 2026-09-14

    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.current = datetime.date(2026, 9, 14)

    def _week(self, weeks_ago, active, enrolled=10):
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'],
            week_start=self.current - datetime.timedelta(weeks=weeks_ago),
            active_parents=active, enrolled_students=enrolled, computed_at=timezone.now(),
        )

    def _result(self, **kwargs):
        (row,) = get_health_metrics(today=self.TODAY, **kwargs)
        return row

    def test_three_consecutive_drops_over_completed_weeks_flag_the_school(self):
        for weeks_ago, active in [(4, 8), (3, 6), (2, 5), (1, 4)]:
            self._week(weeks_ago, active)
        self._week(0, 9)  # the in-progress week is ignored for the flag
        row = self._result()
        self.assertTrue(row['at_risk'])
        self.assertEqual(row['latest_wau_pct'], 40.0)
        self.assertEqual(row['school_id'], self.fx['school'].id)
        self.assertEqual(row['foundation_id'], self.fid)

    def test_a_gap_in_the_completed_weeks_is_not_flagged(self):
        for weeks_ago, active in [(4, 8), (2, 5), (1, 4)]:
            self._week(weeks_ago, active)
        self.assertFalse(self._result()['at_risk'])

    def test_weeks_are_oldest_first_and_only_the_current_one_is_incomplete(self):
        self._week(1, 4)
        self._week(0, 3)
        weeks = self._result()['weeks']
        self.assertEqual([w['week_start'] for w in weeks], ['2026-09-07', '2026-09-14'])
        self.assertEqual([w['complete'] for w in weeks], [True, False])
        self.assertEqual(weeks[0], {
            'week_start': '2026-09-07', 'active_parents': 4, 'enrolled_students': 10, 'wau_pct': 40.0, 'complete': True,
        })

    def test_a_school_with_no_enrolled_students_has_no_percentage(self):
        self._week(1, 0, enrolled=0)
        row = self._result()
        self.assertIsNone(row['weeks'][0]['wau_pct'])
        self.assertIsNone(row['latest_wau_pct'])

    def test_foundation_filter(self):
        self._week(1, 4)
        self.assertEqual(get_health_metrics(foundation_id=self.fid + 999, today=self.TODAY), [])
        self.assertEqual(len(get_health_metrics(foundation_id=self.fid, today=self.TODAY)), 1)

    def test_weeks_older_than_the_window_are_left_out(self):
        self._week(9, 5)
        self.assertEqual(get_health_metrics(today=self.TODAY), [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_health_metrics -v 2`
Expected: ImportError (`apps.reporting.health`).

- [ ] **Step 3: Implement**

Create `apps/reporting/health.py`:

```python
"""Platform health metrics read model (spec/15 RPT-012, RPT-014).

Read-only over `rpt_parent_weekly_activity`. The at-risk flag is derived at read time so a
threshold change never needs a backfill.
"""
from datetime import timedelta

from django.utils import timezone

from apps.reporting.models import RptParentWeeklyActivity

HISTORY_WEEKS = 8      # completed weeks returned, plus the in-progress one
DECLINE_STREAK = 3     # consecutive week-over-week drops that flag a school (RPT-014)


def _ratio(row):
    return row.active_parents / row.enrolled_students if row.enrolled_students else None


def _pct(ratio):
    return None if ratio is None else round(ratio * 100, 1)


def is_declining(ratios) -> bool:
    """`ratios` oldest to newest completed weeks (None = no data). True when the last
    DECLINE_STREAK + 1 values fall strictly at every step."""
    tail = list(ratios)[-(DECLINE_STREAK + 1):]
    if len(tail) < DECLINE_STREAK + 1 or any(r is None for r in tail):
        return False
    return all(later < earlier for earlier, later in zip(tail, tail[1:]))


def get_health_metrics(foundation_id=None, today=None) -> list:
    today = today or timezone.localdate()
    current_week = today - timedelta(days=today.weekday())
    oldest = current_week - timedelta(weeks=HISTORY_WEEKS)

    rows = RptParentWeeklyActivity.all_tenants.filter(
        week_start__gte=oldest, week_start__lte=current_week,
    ).select_related('school').order_by('week_start')
    if foundation_id is not None:
        rows = rows.filter(foundation_id=foundation_id)

    by_school = {}
    for row in rows:
        by_school.setdefault(row.school_id, []).append(row)

    expected = [current_week - timedelta(weeks=n) for n in range(DECLINE_STREAK + 1, 0, -1)]
    result = []
    for school_rows in by_school.values():
        by_week = {r.week_start: r for r in school_rows}
        completed = [r for r in school_rows if r.week_start < current_week]
        ratios = [_ratio(by_week[w]) if w in by_week else None for w in expected]
        result.append({
            'foundation_id': school_rows[0].foundation_id,
            'school_id': school_rows[0].school_id,
            'school_name': school_rows[0].school.name,
            'latest_wau_pct': _pct(_ratio(completed[-1])) if completed else None,
            'at_risk': is_declining(ratios),
            'weeks': [{
                'week_start': r.week_start.isoformat(),
                'active_parents': r.active_parents,
                'enrolled_students': r.enrolled_students,
                'wau_pct': _pct(_ratio(r)),
                'complete': r.week_start < current_week,
            } for r in school_rows],
        })
    return sorted(result, key=lambda s: (s['foundation_id'], s['school_name']))
```

- [ ] **Step 4: Run tests**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_health_metrics -v 1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/reporting/health.py apps/reporting/tests/test_health_metrics.py
git commit -m "feat(reporting): weekly parent WAU read model and 3-week decline flag (RPT-012, RPT-014)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Platform permission, endpoint, docs

**Files:**
- Modify: `apps/identity/rbac.py` (`PLATFORM_ROLE_PERMISSIONS`)
- Modify: `apps/reporting/views.py` (append view; imports)
- Create: `apps/reporting/internal_urls.py`
- Modify: `educore/urls.py` (after the `metering` include)
- Create: `apps/reporting/tests/test_health_metrics_endpoint.py`
- Modify: `memory/01_PROJECT.md`

**Interfaces:**
- Consumes: `health.get_health_metrics` (Task 3).
- Produces: `GET /api/v1/internal/health-metrics/?foundation_id=<int>` returning `{"schools": [...]}`; permission key `platform.health.read`.

- [ ] **Step 1: Write the failing tests**

Create `apps/reporting/tests/test_health_metrics_endpoint.py`:

```python
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import PlatformRoleAssignment, RoleAssignment, User
from apps.identity.rbac import has_platform_permission
from apps.reporting.models import RptParentWeeklyActivity

URL = '/api/v1/internal/health-metrics/'


class HealthMetricsEndpointTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.fid = self.fx['foundation'].id
        self.operator = User.all_tenants.create_user(
            phone_e164='+6281200000701', full_name='Ops', foundation_id=self.fid,
        )
        PlatformRoleAssignment.objects.create(user=self.operator, role=PlatformRoleAssignment.ROLE_PLATFORM_OPERATOR)
        self.admin = User.all_tenants.create_user(
            phone_e164='+6281200000702', full_name='Admin Yayasan', foundation_id=self.fid,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fid, user=self.admin, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.fid,
        )
        week = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday())
        RptParentWeeklyActivity.all_tenants.create(
            foundation_id=self.fid, school=self.fx['school'], week_start=week - datetime.timedelta(weeks=1),
            active_parents=4, enrolled_students=10, computed_at=timezone.now(),
        )
        self.client = APIClient()

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(user).access_token}')

    def test_operator_gets_the_per_school_series(self):
        self._auth(self.operator)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        (school,) = response.json()['schools']
        self.assertEqual(school['school_id'], self.fx['school'].id)
        self.assertEqual(school['latest_wau_pct'], 40.0)
        self.assertFalse(school['at_risk'])

    def test_foundation_filter(self):
        self._auth(self.operator)
        self.assertEqual(self.client.get(URL, {'foundation_id': self.fid + 999}).json(), {'schools': []})
        self.assertEqual(len(self.client.get(URL, {'foundation_id': self.fid}).json()['schools']), 1)

    def test_a_non_numeric_foundation_id_is_a_400(self):
        self._auth(self.operator)
        self.assertEqual(self.client.get(URL, {'foundation_id': 'abc'}).status_code, 400)

    def test_foundation_admin_is_forbidden(self):
        self._auth(self.admin)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_anonymous_is_unauthorized(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_platform_operator_holds_the_permission_and_plain_users_do_not(self):
        self.assertTrue(has_platform_permission(self.operator, 'platform.health.read'))
        self.assertFalse(has_platform_permission(self.admin, 'platform.health.read'))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_health_metrics_endpoint -v 2`
Expected: FAIL (404 on the URL, permission missing).

- [ ] **Step 3: Add the permission**

In `apps/identity/rbac.py` change:

```python
PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    PLATFORM_ROLE_OPERATOR: {'status.write', 'platform.health.read'},
}
```

- [ ] **Step 4: Add the view and route**

Append to `apps/reporting/views.py` (add `from apps.reporting.health import get_health_metrics` to the imports):

```python
class HealthMetricsView(APIView):
    """GET /internal/health-metrics/?foundation_id= (spec/15 §6, RPT-012, RPT-014; platform role only).

    Deliberately not a tenant-derived viewset: the platform role has no tenant context and reads
    every foundation (or one, via `foundation_id`) through `all_tenants` in the read model, so the
    cross-tenant-404 harness does not apply. Access is gated by the platform permission alone.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'platform.health.read'

    def get(self, request):
        raw = request.query_params.get('foundation_id')
        foundation_id = None
        if raw not in (None, ''):
            if not raw.isdigit():
                return Response({'error': _("Parameter foundation_id tidak valid.")}, status=status.HTTP_400_BAD_REQUEST)
            foundation_id = int(raw)
        return Response({'schools': get_health_metrics(foundation_id=foundation_id)})
```

Create `apps/reporting/internal_urls.py`:

```python
from django.urls import path

from apps.reporting.views import HealthMetricsView

urlpatterns = [
    path('health-metrics/', HealthMetricsView.as_view(), name='internal-health-metrics'),
]
```

In `educore/urls.py` after the `metering` line add:

```python
    path('api/v1/internal/', include('apps.reporting.internal_urls')),
```

- [ ] **Step 5: Run the targeted tests, including the RBAC and tenancy guards**

Run: `EDUCORE_USE_SQLITE=1 python manage.py test apps.reporting.tests.test_health_metrics_endpoint apps.identity.tests.test_platform_rbac apps.identity.tests.test_rbac_matrix apps.identity.tests.test_platform_role_assignment -v 1`
Expected: PASS. If `test_rbac_matrix` compares against a committed snapshot that now lacks `platform.health.read`, update the snapshot the way `docs/superpowers/plans/2026-09-19-rbac-sheet-sync.md` describes; do not weaken the test.

- [ ] **Step 6: Update living state**

In `memory/01_PROJECT.md`: demote the current "Current Step" to "Preceding Step" and add a new "Current Step" entry: `[Open Item] Platform health metrics slice 1 [PR open — branch claude/platform-health-metrics]` covering the activity signal (`User.last_activity_date` + `UserActivityDay`, recorded in `EduCoreJWTAuthentication`), the `rpt_parent_weekly_activity` rollup (`refresh_reporting`, frozen weeks), the at-risk rule, the endpoint and `platform.health.read`, the RPT-007 helper extraction, and the not-done items (RPT-013 rest, RPT-016 are Notion Todos; no platform operator exists on PRD).

- [ ] **Step 7: Targeted final verification, then commit**

Run: `EDUCORE_USE_SQLITE=1 python manage.py check && EDUCORE_USE_SQLITE=1 python manage.py makemigrations --check --dry-run`
Expected: no issues, no pending migrations.

```bash
git add apps educore memory
git commit -m "feat(reporting): GET /internal/health-metrics for the platform role (RPT-012, RPT-014)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

Then follow the AGENTS.md pre-PR protocol (fetch, rebase on `origin/main`, targeted tests only, `git push --force-with-lease`), open the PR, and let CI run the full suites (SQLite and MySQL 8).

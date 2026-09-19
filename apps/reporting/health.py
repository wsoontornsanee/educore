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

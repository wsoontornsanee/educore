"""Platform health metrics read model (spec/15 RPT-012, RPT-013, RPT-014).

Read-only over `rpt_parent_weekly_activity`. The at-risk flag is derived at read time so a
threshold change never needs a backfill.
"""
from datetime import timedelta

from django.utils import timezone

from apps.reporting.models import RptParentWeeklyActivity

HISTORY_WEEKS = 8      # completed weeks returned, plus the in-progress one
DECLINE_STREAK = 3     # consecutive week-over-week drops that flag a school (RPT-014)


def _over(numerator, denominator):
    """None when the week has no data for the metric (column not computed yet, or a zero base)."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


# Metric name (the key in the response) -> ratio of one weekly row. A metric is "declining" for RPT-014
# on its own, so a school flagged at risk says which health signal is falling.
METRICS = {
    'wau_pct': lambda r: _over(r.active_parents, r.enrolled_students),
    'collection_rate_pct': lambda r: _over(r.collection_collected, r.collection_billed),
    'attendance_compliance_pct': lambda r: _over(r.attendance_submitted_periods, r.attendance_expected_periods),
    'gate_uptime_pct': lambda r: _over(r.gate_up_samples, r.gate_samples),
    'canteen_adoption_pct': lambda r: _over(r.canteen_active_students, r.enrolled_students),
}


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
        week_start__gte=oldest, week_start__lte=current_week, school__deleted_at__isnull=True,
    ).select_related('school').order_by('week_start')
    if foundation_id is not None:
        rows = rows.filter(foundation_id=foundation_id)

    by_school = {}
    for row in rows:
        by_school.setdefault(row.school_id, []).append(row)

    previous_week = current_week - timedelta(weeks=1)
    expected = [current_week - timedelta(weeks=n) for n in range(DECLINE_STREAK + 1, 0, -1)]
    result = []
    for school_rows in by_school.values():
        by_week = {r.week_start: r for r in school_rows}
        declining = [
            name for name, ratio in METRICS.items()
            if is_declining([ratio(by_week[w]) if w in by_week else None for w in expected])
        ]
        result.append({
            'foundation_id': school_rows[0].foundation_id,
            'school_id': school_rows[0].school_id,
            'school_name': school_rows[0].school.name,
            'latest_wau_pct': _pct(METRICS['wau_pct'](by_week[previous_week])) if previous_week in by_week else None,
            'at_risk': bool(declining),
            'declining_metrics': declining,
            'weeks': [{
                'week_start': r.week_start.isoformat(),
                'active_parents': r.active_parents,
                'enrolled_students': r.enrolled_students,
                **{name: _pct(ratio(r)) for name, ratio in METRICS.items()},
                'complete': r.week_start < current_week,
            } for r in school_rows],
        })
    return sorted(result, key=lambda s: (s['foundation_id'], s['school_name']))

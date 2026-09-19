"""Platform health metrics read model (spec/15 RPT-012, RPT-013, RPT-014, RPT-016).

WAU and the at-risk flag are read from `rpt_parent_weekly_activity`. The at-risk flag and the
time-to-value milestones are derived at read time so a threshold change never needs a backfill.
"""
import math
from datetime import timedelta

from django.db.models import Min
from django.utils import timezone

from apps.attendance.models import GateEvent, GateEventStatus
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, UserActivityDay
from apps.reporting.models import RptParentWeeklyActivity
from apps.reporting.services import _active_enrolled_student_ids, _counted_student_ids, _parent_user_ids

HISTORY_WEEKS = 8      # completed weeks returned, plus the in-progress one
DECLINE_STREAK = 3     # consecutive week-over-week drops that flag a school (RPT-014)
ACTIVATION_SHARE = 0.5  # parent accounts that have logged in, over enrolled students (RPT-016)


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


def _milestone(day, contract_date):
    return {'date': day.isoformat() if day else None,
            'days': (day - contract_date).days if day and contract_date else None}


def _time_to_value(school, contract_date, enrolled_ids) -> dict:
    """RPT-016: when the school first scanned at a gate, was invoiced, had a parent log in, and had
    half its enrolled students covered by a parent account that has logged in.

    Logins come from `UserActivityDay`, which only exists from the day it shipped, so a school that
    was already live then shows a first login no earlier than that. Activation counts parent accounts
    against enrolled students (the same ratio as the WAU north star), against the school's current
    roster: past rosters are not kept.
    """
    scope = {'foundation_id': school.foundation_id, 'school': school, 'deleted_at__isnull': True}
    first_scan = GateEvent.all_tenants.filter(status=GateEventStatus.ACCEPTED, **scope).aggregate(m=Min('occurred_at'))['m']
    first_invoice = Invoice.all_tenants.filter(**scope).exclude(status=InvoiceStatus.DRAFT).aggregate(m=Min('issue_date'))['m']

    counted = _counted_student_ids(school, enrolled_ids)
    first_logins = sorted(
        UserActivityDay.all_tenants.filter(
            foundation_id=school.foundation_id, user_id__in=_parent_user_ids(school, counted), deleted_at__isnull=True,
        ).values('user_id').annotate(first=Min('date')).order_by().values_list('first', flat=True)
    )
    needed = math.ceil(len(counted) * ACTIVATION_SHARE)  # 0 when nobody is enrolled
    activated = first_logins[needed - 1] if needed and len(first_logins) >= needed else None

    return {
        'contract_date': contract_date.isoformat() if contract_date else None,
        'first_gate_scan': _milestone(timezone.localtime(first_scan).date() if first_scan else None, contract_date),
        'first_invoice': _milestone(first_invoice, contract_date),
        'first_parent_login': _milestone(first_logins[0] if first_logins else None, contract_date),
        'parent_activation_50': _milestone(activated, contract_date),
    }


def get_health_metrics(foundation_id=None, today=None) -> list:
    today = today or timezone.localdate()
    current_week = today - timedelta(days=today.weekday())
    oldest = current_week - timedelta(weeks=HISTORY_WEEKS)

    rows = RptParentWeeklyActivity.all_tenants.filter(
        week_start__gte=oldest, week_start__lte=current_week, school__deleted_at__isnull=True,
    ).select_related('school').order_by('week_start')
    if foundation_id is not None:
        rows = rows.filter(foundation_id=foundation_id)

    rows_list = list(rows)
    by_school = {}
    for row in rows_list:
        by_school.setdefault(row.school_id, []).append(row)

    contract_dates = dict(Foundation.objects.filter(
        id__in={r.foundation_id for r in rows_list}).values_list('id', 'contract_date'))
    enrolled_ids = _active_enrolled_student_ids()

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
            'time_to_value': _time_to_value(
                school_rows[0].school, contract_dates.get(school_rows[0].foundation_id), enrolled_ids),
            'weeks': [{
                'week_start': r.week_start.isoformat(),
                'active_parents': r.active_parents,
                'enrolled_students': r.enrolled_students,
                **{name: _pct(ratio(r)) for name, ratio in METRICS.items()},
                'complete': r.week_start < current_week,
            } for r in school_rows],
        })
    return sorted(result, key=lambda s: (s['foundation_id'], s['school_name']))

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.identity.models import School
from apps.reporting.models import RptDailyAttendance, RptWalletActivity

DASHBOARD_LOOKBACK_DAYS = 2


def _report_start_date(scope: str, since, earliest_date):
    """Shared date-range rule for every rpt_* refresh: dashboard = last
    DASHBOARD_LOOKBACK_DAYS days (incremental, every 5 min per spec/15 §2), full =
    everything back to the earliest relevant row (nightly rebuild). `since` overrides
    either, mainly for tests.
    """
    today = timezone.now().date()
    if since is not None:
        return since
    if scope == 'dashboard':
        return today - timedelta(days=DASHBOARD_LOOKBACK_DAYS)
    return earliest_date or today


def refresh_wallet_activity(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_wallet_activity, one row per school per day.

    scope='dashboard': only the last DASHBOARD_LOOKBACK_DAYS days (incremental,
    every 5 minutes per spec/15 §2). scope='full': every day that has any wallet
    activity at all (nightly rebuild). `since` overrides the computed start date
    for either scope, mainly for tests.
    """
    from apps.wallet.models import POSTransaction, POSTransactionStatus, WalletTransaction, WalletTransactionStatus, WalletTransactionType

    now = timezone.now()
    earliest_topup = None
    if since is None and scope != 'dashboard':
        earliest_topup = WalletTransaction.all_tenants.filter(
            type=WalletTransactionType.TOPUP, deleted_at__isnull=True,
        ).order_by('occurred_at').values_list('occurred_at__date', flat=True).first()
    start_date = _report_start_date(scope, since, earliest_topup)

    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        topups_by_day = dict(
            WalletTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                wallet__student__school=school,
                type=WalletTransactionType.TOPUP,
                status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('amount')).values_list('occurred_at__date', 'total')
        )
        purchases_by_day = dict(
            POSTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                merchant__school=school,
                status=POSTransactionStatus.COMPLETED,
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('subtotal')).values_list('occurred_at__date', 'total')
        )
        commission_by_day = dict(
            POSTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                merchant__school=school,
                status=POSTransactionStatus.COMPLETED,
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(total=Sum('commission')).values_list('occurred_at__date', 'total')
        )
        active_wallets_by_day = dict(
            WalletTransaction.all_tenants.filter(
                foundation_id=school.foundation_id,
                wallet__student__school=school,
                status__in=[WalletTransactionStatus.COMPLETED, WalletTransactionStatus.RECONCILE_REQUIRED],
                occurred_at__date__gte=start_date,
                deleted_at__isnull=True,
            ).values_list('occurred_at__date').annotate(count=Count('wallet_id', distinct=True)).values_list('occurred_at__date', 'count')
        )

        days = set(topups_by_day) | set(purchases_by_day) | set(commission_by_day) | set(active_wallets_by_day)
        for day in days:
            RptWalletActivity.all_tenants.update_or_create(
                foundation_id=school.foundation_id,
                school=school,
                date=day,
                currency='IDR',
                defaults={
                    'topups': topups_by_day.get(day, Decimal('0.00')),
                    'purchases': purchases_by_day.get(day, Decimal('0.00')),
                    'commission': commission_by_day.get(day, Decimal('0.00')),
                    'active_wallets': active_wallets_by_day.get(day, 0),
                    'computed_at': now,
                },
            )
            rows_written += 1

    return {'rows_written': rows_written, 'start_date': str(start_date), 'scope': scope}


def refresh_daily_attendance(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_daily_attendance, one row per school per class per day.

    Joins AttendanceDay to each student's CURRENT active ClassEnrollment (this repo
    has no historical as-of-date enrollment tracking, only `is_active` — same
    resolution every other cross-app "which class is this student in" join already
    uses). A student with no active class enrollment is skipped. DISPEN is bucketed
    into `permitted` alongside IZIN — spec's 5 named columns have no 6th slot, and
    dispensasi is a sanctioned absence, the closest existing bucket.
    """
    from apps.academic.models import ClassEnrollment
    from apps.attendance.models import AttendanceDay, AttendanceStatus

    now = timezone.now()
    earliest_day = None
    if since is None and scope != 'dashboard':
        earliest_day = AttendanceDay.all_tenants.filter(deleted_at__isnull=True).order_by('date').values_list('date', flat=True).first()
    start_date = _report_start_date(scope, since, earliest_day)

    class_group_by_student = dict(
        ClassEnrollment.all_tenants.filter(is_active=True, deleted_at__isnull=True).values_list('student_id', 'class_group_id')
    )

    counts = {}
    for foundation_id, school_id, student_id, date, status in AttendanceDay.all_tenants.filter(
        date__gte=start_date, deleted_at__isnull=True,
    ).values_list('foundation_id', 'school_id', 'student_id', 'date', 'status'):
        class_group_id = class_group_by_student.get(student_id)
        if class_group_id is None:
            continue
        key = (foundation_id, school_id, date, class_group_id)
        counts.setdefault(key, {}).setdefault(status, 0)
        counts[key][status] += 1

    rows_written = 0
    for (foundation_id, school_id, date, class_group_id), status_counts in counts.items():
        present = status_counts.get(AttendanceStatus.HADIR, 0)
        late = status_counts.get(AttendanceStatus.TERLAMBAT, 0)
        sick = status_counts.get(AttendanceStatus.SAKIT, 0)
        permitted = status_counts.get(AttendanceStatus.IZIN, 0) + status_counts.get(AttendanceStatus.DISPEN, 0)
        absent = status_counts.get(AttendanceStatus.ALPA, 0)
        total = present + late + sick + permitted + absent
        rate_pct = ((Decimal(present + late) / Decimal(total)) * Decimal('100')).quantize(Decimal('0.01')) if total else Decimal('0.00')

        RptDailyAttendance.all_tenants.update_or_create(
            foundation_id=foundation_id,
            school_id=school_id,
            date=date,
            class_group_id=class_group_id,
            defaults={
                'present': present, 'late': late, 'sick': sick, 'permitted': permitted, 'absent': absent,
                'rate_pct': rate_pct, 'computed_at': now,
            },
        )
        rows_written += 1

    return {'rows_written': rows_written, 'start_date': str(start_date), 'scope': scope}

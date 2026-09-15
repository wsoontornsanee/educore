from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.identity.models import School
from apps.reporting.models import (
    RptAcademicPerformance,
    RptActiveStudent,
    RptDailyAttendance,
    RptDailyFinance,
    RptWalletActivity,
)

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


def refresh_academic_performance(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_academic_performance, one row per school/term/class/subject.

    Only counts scores from PUBLISHED assessments — matches this codebase's existing
    semantics elsewhere (an unpublished assessment's scores are draft, not final).
    No day-based lookback to reuse here (grades are term-scoped, not day-bucketed):
    scope='dashboard' recomputes only currently-active terms (Term.is_active=True);
    scope='full' recomputes every term with any published score.
    """
    from apps.academic.models import Assessment, AssessmentScore, Term

    now = timezone.now()

    terms = Term.all_tenants.filter(deleted_at__isnull=True)
    if since is None and scope == 'dashboard':
        terms = terms.filter(is_active=True)
    term_ids = list(terms.values_list('id', flat=True))

    rows_written = 0
    scores = AssessmentScore.all_tenants.filter(
        assessment__published=True,
        assessment__class_subject__term_id__in=term_ids,
        score__isnull=False,
        deleted_at__isnull=True,
    ).values_list(
        'foundation_id',
        'assessment__class_subject__class_group__school_id',
        'assessment__class_subject__term_id',
        'assessment__class_subject__class_group_id',
        'assessment__class_subject__subject_id',
        'score', 'descriptor',
    )

    grouped = {}
    for foundation_id, school_id, term_id, class_group_id, subject_id, score, descriptor in scores:
        key = (foundation_id, school_id, term_id, class_group_id, subject_id)
        bucket = grouped.setdefault(key, {'scores': [], 'bands': {}})
        bucket['scores'].append(score)
        bucket['bands'][descriptor] = bucket['bands'].get(descriptor, 0) + 1

    for (foundation_id, school_id, term_id, class_group_id, subject_id), bucket in grouped.items():
        avg_score = (sum(bucket['scores']) / len(bucket['scores'])).quantize(Decimal('0.01'))

        RptAcademicPerformance.all_tenants.update_or_create(
            foundation_id=foundation_id,
            school_id=school_id,
            term_id=term_id,
            class_group_id=class_group_id,
            subject_id=subject_id,
            defaults={
                'avg_score': avg_score,
                'band_distribution': bucket['bands'],
                'computed_at': now,
            },
        )
        rows_written += 1

    return {'rows_written': rows_written, 'scope': scope}


def _month_is_closed(month, today) -> bool:
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_day_of_month = next_month - timedelta(days=1)
    return last_day_of_month < today


def refresh_active_students(scope: str, since=None) -> dict:
    """spec/15 §2, RPT-007/RPT-008: rebuild rpt_active_students, one row per school
    per month — the invoice basis for EduCore's own subscription billing.

    "Active" (RPT-007) = current Student.status == ACTIVE AND has a current active
    ClassEnrollment (this repo has no historical point-in-time status/enrollment
    tracking, only "now" — same documented simplification as every other rollup in
    this app; the count is accurate at the moment each month's cron actually runs,
    which is exactly what makes the next requirement correct).

    RPT-008 (immutability): once a month has fully closed, an EXISTING row for it is
    NEVER recomputed — only the still-open current month is refreshed repeatedly.
    scope='dashboard' only touches the current month; scope='full' also backfills the
    most recently closed month if it has no row yet (bounded backfill, not unbounded
    history — a nightly refresh only ever needs to freeze the month that JUST closed).
    """
    from apps.academic.models import ClassEnrollment
    from apps.identity.models import Student

    now = timezone.now()
    today = now.date()
    current_month = today.replace(day=1)

    if since is not None:
        target_months = [since]
    elif scope == 'dashboard':
        target_months = [current_month]
    else:
        previous_month = (current_month - timedelta(days=1)).replace(day=1)
        target_months = [current_month, previous_month]

    active_student_ids = set(
        ClassEnrollment.all_tenants.filter(is_active=True, deleted_at__isnull=True).values_list('student_id', flat=True)
    )

    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        for month in target_months:
            existing = RptActiveStudent.all_tenants.filter(
                foundation_id=school.foundation_id, school=school, month=month,
            ).first()
            if existing and _month_is_closed(month, today):
                continue  # RPT-008: a closed month's row is frozen forever

            active_count = Student.all_tenants.filter(
                foundation_id=school.foundation_id, school=school,
                status=Student.STATUS_ACTIVE, id__in=active_student_ids,
                deleted_at__isnull=True,
            ).count()

            RptActiveStudent.all_tenants.update_or_create(
                foundation_id=school.foundation_id,
                school=school,
                month=month,
                defaults={'active_count': active_count, 'computed_at': now},
            )
            rows_written += 1

    return {'rows_written': rows_written, 'scope': scope}


def refresh_daily_finance(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_daily_finance, one row per school per day.

    billed/collected/fees/payments_count are true per-day deltas from Invoice/Payment
    event logs. outstanding is a live snapshot (see RptDailyFinance docstring) —
    computed once per school per refresh call and attached identically to every
    day-row written in that run.
    """
    from apps.finance.models import Invoice, InvoiceStatus, Payment, PaymentStatus

    now = timezone.now()
    earliest_invoice = None
    if since is None and scope != 'dashboard':
        earliest_invoice = Invoice.all_tenants.filter(deleted_at__isnull=True).order_by('issue_date').values_list('issue_date', flat=True).first()
    start_date = _report_start_date(scope, since, earliest_invoice)

    NON_FINAL_STATUSES = [InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID]

    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        billed_by_day = dict(
            Invoice.all_tenants.filter(
                foundation_id=school.foundation_id, school=school,
                issue_date__gte=start_date, deleted_at__isnull=True,
            ).values_list('issue_date').annotate(total=Sum('total')).values_list('issue_date', 'total')
        )
        settled_payments = Payment.all_tenants.filter(
            foundation_id=school.foundation_id, school=school, status=PaymentStatus.SETTLED,
            settled_at__date__gte=start_date, deleted_at__isnull=True,
        )
        collected_by_day = dict(
            settled_payments.values_list('settled_at__date').annotate(total=Sum('amount')).values_list('settled_at__date', 'total')
        )
        fees_by_day = dict(
            settled_payments.values_list('settled_at__date').annotate(total=Sum('fee')).values_list('settled_at__date', 'total')
        )
        payments_count_by_day = dict(
            settled_payments.values_list('settled_at__date').annotate(count=Count('id')).values_list('settled_at__date', 'count')
        )

        outstanding = Invoice.all_tenants.filter(
            foundation_id=school.foundation_id, school=school,
            status__in=NON_FINAL_STATUSES, deleted_at__isnull=True,
        ).aggregate(total=Sum('total'))['total'] or Decimal('0.00')
        paid_on_open_invoices = Invoice.all_tenants.filter(
            foundation_id=school.foundation_id, school=school,
            status__in=NON_FINAL_STATUSES, deleted_at__isnull=True,
        ).aggregate(total=Sum('paid'))['total'] or Decimal('0.00')
        outstanding -= paid_on_open_invoices

        days = set(billed_by_day) | set(collected_by_day) | set(fees_by_day) | set(payments_count_by_day)
        for day in days:
            RptDailyFinance.all_tenants.update_or_create(
                foundation_id=school.foundation_id,
                school=school,
                date=day,
                currency='IDR',
                defaults={
                    'billed': billed_by_day.get(day, Decimal('0.00')),
                    'collected': collected_by_day.get(day, Decimal('0.00')),
                    'fees': fees_by_day.get(day, Decimal('0.00')),
                    'payments_count': payments_count_by_day.get(day, 0),
                    'outstanding': outstanding,
                    'computed_at': now,
                },
            )
            rows_written += 1

    return {'rows_written': rows_written, 'start_date': str(start_date), 'scope': scope}

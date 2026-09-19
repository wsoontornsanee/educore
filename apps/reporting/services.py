import calendar
import datetime
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.core.archiving import policy_for
from apps.identity.models import School
from apps.reporting.models import (
    RptAcademicPerformance,
    RptActiveStudent,
    RptActiveStudentRoster,
    RptArAging,
    RptDailyAttendance,
    RptDailyFinance,
    RptSubscriptionCharge,
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


def _first_fully_hot_day(*model_labels):
    """First day the hot tables still hold in full, given NFR-009 archiving.

    Rows up to some day have moved to `<table>_archive`, so rebuilding those days from
    the hot tables would overwrite a correct rollup with a partial one. Days at or before
    the newest archived row keep the rollup written while they were still hot.
    """
    newest = [t for t in (policy_for(label).archived_through() for label in model_labels) if t]
    if not newest:
        return datetime.date.min
    return timezone.localtime(max(newest)).date() + timedelta(days=1)


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
    start_date = max(start_date, _first_fully_hot_day('wallet.WalletTransaction', 'wallet.POSTransaction'))

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

            counted_ids = sorted(Student.all_tenants.filter(
                foundation_id=school.foundation_id, school=school,
                status=Student.STATUS_ACTIVE, id__in=active_student_ids,
                deleted_at__isnull=True,
            ).values_list('id', flat=True))

            # The count and the roster it was made from are written together, so they can never disagree
            # (RPT-009), and they freeze together (the `existing` check above covers both).
            with transaction.atomic():
                RptActiveStudent.all_tenants.update_or_create(
                    foundation_id=school.foundation_id,
                    school=school,
                    month=month,
                    defaults={'active_count': len(counted_ids), 'computed_at': now},
                )
                RptActiveStudentRoster.all_tenants.update_or_create(
                    foundation_id=school.foundation_id,
                    school=school,
                    month=month,
                    defaults={'student_ids': counted_ids, 'captured_at': now},
                )
            rows_written += 1

    return {'rows_written': rows_written, 'scope': scope}


def _month_days(month) -> tuple[datetime.date, int]:
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    return month.replace(day=days_in_month), days_in_month


def refresh_subscription_charges(scope: str, since=None) -> dict:
    """RPT-010: one `RptSubscriptionCharge` per school, month and priced module, from the month's
    counted students, the price list (`ModulePrice`) and the days the module was entitled.

    Runs after `refresh_active_students` and reads its count; a school-month with no count has no
    charge. The price is the foundation's CURRENT tier's, looked up at the month (the repo keeps no
    tier history), and is stored on the row. A month is rewritten until the first run after it has
    ended and then frozen (RPT-008), so entitlement changes made on its last day are still seen.
    A module with no price for that tier and month gets no row: it is not charged.
    """
    from apps.identity.entitlements import entitled_days_in_month
    from apps.identity.models import Foundation, ModulePrice

    now = timezone.now()
    today = now.date()
    current_month = today.replace(day=1)
    if since is not None:
        target_months = [since]
    elif scope == 'dashboard':
        target_months = [current_month]
    else:
        target_months = [current_month, (current_month - timedelta(days=1)).replace(day=1)]

    tiers = dict(Foundation.objects.values_list('id', 'plan_tier'))
    rows_written = 0
    for school in School.all_tenants.filter(deleted_at__isnull=True):
        for month in target_months:
            count_row = RptActiveStudent.all_tenants.filter(
                foundation_id=school.foundation_id, school=school, month=month,
            ).first()
            if count_row is None:
                continue
            last_day, days_in_month = _month_days(month)
            existing = list(RptSubscriptionCharge.all_tenants.filter(
                foundation_id=school.foundation_id, school=school, month=month,
            ))
            if existing and all(row.computed_at.date() > last_day for row in existing):
                continue  # RPT-008: computed after the month ended, so frozen

            plan_tier = tiers[school.foundation_id]
            days = entitled_days_in_month(school.foundation_id, school.id, month)
            prices = {}
            for price in ModulePrice.objects.filter(
                plan_tier=plan_tier, currency=school.base_currency, effective_from__lte=month,
            ).order_by('effective_from'):
                prices[price.module_key] = price  # the latest start wins

            with transaction.atomic():
                for module_key, price in prices.items():
                    # Full precision, rounded once to the stored scale (CUR-017).
                    amount = (
                        Decimal(count_row.active_count) * price.unit_price * days[module_key] / days_in_month
                    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    RptSubscriptionCharge.all_tenants.update_or_create(
                        foundation_id=school.foundation_id, school=school, month=month, module_key=module_key,
                        defaults={
                            'plan_tier': plan_tier, 'currency': price.currency, 'active_count': count_row.active_count,
                            'unit_price': price.unit_price, 'active_days': days[module_key],
                            'days_in_month': days_in_month, 'amount': amount, 'computed_at': now,
                        },
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


def refresh_foundation_kpis(scope: str, since=None) -> dict:
    """spec/03 §4, spec/15 §2: rebuild rpt_foundation_kpis, one row per school per
    month plus one foundation-wide aggregate row (school=None) per month.

    Sourced entirely from the other already-refreshed rpt_* tables (FND-005: never
    live transactional joins) — rpt_daily_finance, rpt_ar_aging, rpt_active_students,
    rpt_daily_attendance, rpt_wallet_activity. rpt_ar_aging keeps one snapshot per
    as_of day rather than a period range, so the most recent as_of on or before the
    period's end is used.

    Multi-currency (FND-005b): detects when a foundation's schools use more than one
    base_currency. Per-school rows always use the school's own currency. The aggregate
    row either sums directly (SINGLE_CURRENCY), converts via FxRate (CONSOLIDATED), or
    flags the gap (MIXED_CURRENCY_UNSUPPORTED) — never silently sums wrong (CUR-024).

    scope='dashboard': current month only (matches the 5-minute incremental cadence
    of its source tables). scope='full': current + previous month, same backfill
    depth as refresh_active_students. `since` overrides to a specific month
    (normalized to the 1st), mainly for tests.

    A single advisory-locked cron process is the only writer (refresh_reporting),
    so update_or_create's own get-then-write is sufficient here without a DB-level
    uniqueness constraint on the nullable school_id column.
    """
    from apps.foundation.models import RptFoundationKPI
    from apps.foundation.services import FxRateNotFoundError, convert_currency, has_mixed_currencies
    from apps.identity.models import Foundation

    now = timezone.now()
    today = now.date()
    current_month = today.replace(day=1)

    if since is not None:
        target_months = [since.replace(day=1)]
    elif scope == 'dashboard':
        target_months = [current_month]
    else:
        previous_month = (current_month - timedelta(days=1)).replace(day=1)
        target_months = [current_month, previous_month]

    rows_written = 0
    for foundation in Foundation.objects.all():
        is_mixed = has_mixed_currencies(foundation.id)
        reporting_currency = foundation.reporting_currency or 'IDR'

        for month in target_months:
            month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            period_end = min(month_end, today)

            as_of = RptArAging.all_tenants.filter(
                foundation_id=foundation.id, as_of__lte=period_end,
            ).order_by('-as_of').values_list('as_of', flat=True).first()

            totals = {
                'billed': Decimal('0.00'), 'collected': Decimal('0.00'), 'outstanding': Decimal('0.00'),
                'ar_0_30': Decimal('0.00'), 'ar_31_60': Decimal('0.00'), 'ar_61_90': Decimal('0.00'),
                'ar_90_plus': Decimal('0.00'), 'campus_spend': Decimal('0.00'), 'active_students': 0,
                'present_like': 0, 'attendance_marks': 0,
            }
            agg_fx_rate_date = None
            conversion_failed = False

            for school in School.all_tenants.filter(foundation_id=foundation.id, deleted_at__isnull=True):
                school_currency = school.base_currency or 'IDR'

                finance = RptDailyFinance.all_tenants.filter(
                    foundation_id=foundation.id, school=school, date__gte=month, date__lte=period_end,
                ).aggregate(billed=Sum('billed'), collected=Sum('collected'))
                billed = finance['billed'] or Decimal('0.00')
                collected = finance['collected'] or Decimal('0.00')
                outstanding = RptDailyFinance.all_tenants.filter(
                    foundation_id=foundation.id, school=school, date__lte=period_end,
                ).order_by('-date').values_list('outstanding', flat=True).first() or Decimal('0.00')

                ar_buckets = {'0_30': Decimal('0.00'), '31_60': Decimal('0.00'), '61_90': Decimal('0.00'), '90_PLUS': Decimal('0.00')}
                if as_of is not None:
                    for bucket, amount in RptArAging.all_tenants.filter(
                        foundation_id=foundation.id, school=school, as_of=as_of,
                    ).values_list('bucket').annotate(total=Sum('amount')).values_list('bucket', 'total'):
                        if bucket in ar_buckets:
                            ar_buckets[bucket] = amount

                campus_spend = RptWalletActivity.all_tenants.filter(
                    foundation_id=foundation.id, school=school, date__gte=month, date__lte=period_end,
                ).aggregate(total=Sum('purchases'))['total'] or Decimal('0.00')

                active_students = RptActiveStudent.all_tenants.filter(
                    foundation_id=foundation.id, school=school, month=month,
                ).values_list('active_count', flat=True).first() or 0

                present_like = 0
                attendance_marks = 0
                for present, late, sick, permitted, absent in RptDailyAttendance.all_tenants.filter(
                    foundation_id=foundation.id, school=school, date__gte=month, date__lte=period_end,
                ).values_list('present', 'late', 'sick', 'permitted', 'absent'):
                    present_like += present + late
                    attendance_marks += present + late + sick + permitted + absent
                avg_attendance_pct = (
                    (Decimal(present_like) / Decimal(attendance_marks)) * Decimal('100')
                ).quantize(Decimal('0.01')) if attendance_marks else Decimal('0.00')

                # Per-school row: always in the school's own currency
                RptFoundationKPI.objects.update_or_create(
                    foundation_id=foundation.id, school_id=school.id,
                    period_start=month, period_end=month_end,
                    defaults={
                        'billed': billed, 'collected': collected, 'outstanding': outstanding,
                        'ar_0_30': ar_buckets['0_30'], 'ar_31_60': ar_buckets['31_60'],
                        'ar_61_90': ar_buckets['61_90'], 'ar_90_plus': ar_buckets['90_PLUS'],
                        'campus_spend': campus_spend, 'active_students': active_students,
                        'avg_attendance_pct': avg_attendance_pct,
                        'currency': school_currency,
                        'reporting_currency': reporting_currency,
                        'fx_rate_date': None,
                        'multi_currency_status': RptFoundationKPI.MULTI_CURRENCY_SINGLE,
                    },
                )
                rows_written += 1

                # Accumulate for aggregate row — convert to reporting_currency if mixed
                if is_mixed and school_currency != reporting_currency:
                    try:
                        converted_billed, rate = convert_currency(
                            billed, school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_collected, _ = convert_currency(
                            collected, school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_outstanding, _ = convert_currency(
                            outstanding, school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_ar_0_30, _ = convert_currency(
                            ar_buckets['0_30'], school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_ar_31_60, _ = convert_currency(
                            ar_buckets['31_60'], school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_ar_61_90, _ = convert_currency(
                            ar_buckets['61_90'], school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_ar_90_plus, _ = convert_currency(
                            ar_buckets['90_PLUS'], school_currency, reporting_currency, period_end, foundation.id,
                        )
                        converted_campus_spend, _ = convert_currency(
                            campus_spend, school_currency, reporting_currency, period_end, foundation.id,
                        )
                        if agg_fx_rate_date is None:
                            agg_fx_rate_date = rate.effective_date

                        totals['billed'] += converted_billed
                        totals['collected'] += converted_collected
                        totals['outstanding'] += converted_outstanding
                        totals['ar_0_30'] += converted_ar_0_30
                        totals['ar_31_60'] += converted_ar_31_60
                        totals['ar_61_90'] += converted_ar_61_90
                        totals['ar_90_plus'] += converted_ar_90_plus
                        totals['campus_spend'] += converted_campus_spend
                    except FxRateNotFoundError:
                        conversion_failed = True
                else:
                    # Same currency — sum directly
                    totals['billed'] += billed
                    totals['collected'] += collected
                    totals['outstanding'] += outstanding
                    totals['ar_0_30'] += ar_buckets['0_30']
                    totals['ar_31_60'] += ar_buckets['31_60']
                    totals['ar_61_90'] += ar_buckets['61_90']
                    totals['ar_90_plus'] += ar_buckets['90_PLUS']
                    totals['campus_spend'] += campus_spend

                totals['active_students'] += active_students
                totals['present_like'] += present_like
                totals['attendance_marks'] += attendance_marks

            agg_avg_attendance = (
                (Decimal(totals['present_like']) / Decimal(totals['attendance_marks'])) * Decimal('100')
            ).quantize(Decimal('0.01')) if totals['attendance_marks'] else Decimal('0.00')

            if conversion_failed:
                # CUR-024: missing rate surfaces as an explicit gap, never silent zero
                agg_defaults = {
                    'billed': Decimal('0.00'), 'collected': Decimal('0.00'), 'outstanding': Decimal('0.00'),
                    'ar_0_30': Decimal('0.00'), 'ar_31_60': Decimal('0.00'),
                    'ar_61_90': Decimal('0.00'), 'ar_90_plus': Decimal('0.00'),
                    'campus_spend': Decimal('0.00'), 'active_students': totals['active_students'],
                    'avg_attendance_pct': agg_avg_attendance,
                    'currency': reporting_currency, 'reporting_currency': reporting_currency,
                    'fx_rate_date': None,
                    'multi_currency_status': RptFoundationKPI.MULTI_CURRENCY_UNSUPPORTED,
                }
            elif is_mixed:
                agg_defaults = {
                    'billed': totals['billed'], 'collected': totals['collected'], 'outstanding': totals['outstanding'],
                    'ar_0_30': totals['ar_0_30'], 'ar_31_60': totals['ar_31_60'],
                    'ar_61_90': totals['ar_61_90'], 'ar_90_plus': totals['ar_90_plus'],
                    'campus_spend': totals['campus_spend'], 'active_students': totals['active_students'],
                    'avg_attendance_pct': agg_avg_attendance,
                    'currency': reporting_currency, 'reporting_currency': reporting_currency,
                    'fx_rate_date': agg_fx_rate_date,
                    'multi_currency_status': RptFoundationKPI.MULTI_CURRENCY_CONSOLIDATED,
                }
            else:
                agg_defaults = {
                    'billed': totals['billed'], 'collected': totals['collected'], 'outstanding': totals['outstanding'],
                    'ar_0_30': totals['ar_0_30'], 'ar_31_60': totals['ar_31_60'],
                    'ar_61_90': totals['ar_61_90'], 'ar_90_plus': totals['ar_90_plus'],
                    'campus_spend': totals['campus_spend'], 'active_students': totals['active_students'],
                    'avg_attendance_pct': agg_avg_attendance,
                    'currency': reporting_currency, 'reporting_currency': reporting_currency,
                    'fx_rate_date': None,
                    'multi_currency_status': RptFoundationKPI.MULTI_CURRENCY_SINGLE,
                }

            RptFoundationKPI.objects.update_or_create(
                foundation_id=foundation.id, school_id=None,
                period_start=month, period_end=month_end,
                defaults=agg_defaults,
            )
            rows_written += 1

    return {'rows_written': rows_written, 'scope': scope}


def refresh_ar_aging(scope: str, since=None) -> dict:
    """spec/15 §2: rebuild rpt_ar_aging(school_id, student_id, as_of, bucket, currency, amount).
    Rolls up outstanding open invoices per student per aging bucket as of today.
    """
    from apps.finance.models import Invoice, InvoiceStatus
    from apps.finance.services.ar_aging import get_aging_bucket

    now = timezone.now()
    today = now.date()
    rows_written = 0

    NON_FINAL_STATUSES = [InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID]

    for school in School.all_tenants.filter(deleted_at__isnull=True):
        invoices = Invoice.all_tenants.filter(
            foundation_id=school.foundation_id,
            school=school,
            status__in=NON_FINAL_STATUSES,
            deleted_at__isnull=True,
        ).select_related('student')

        student_buckets: dict[tuple[int, str, str], dict] = {}
        for invoice in invoices:
            balance = invoice.balance_due
            if balance <= Decimal('0.00'):
                continue
            bucket = get_aging_bucket(invoice.due_date, today)
            key = (invoice.student_id, bucket, invoice.currency)
            if key not in student_buckets:
                student_buckets[key] = {'amount': Decimal('0.00'), 'count': 0, 'student': invoice.student}
            student_buckets[key]['amount'] += balance
            student_buckets[key]['count'] += 1

        for (student_id, bucket, currency), data in student_buckets.items():
            RptArAging.all_tenants.update_or_create(
                foundation_id=school.foundation_id,
                school=school,
                student=data['student'],
                as_of=today,
                bucket=bucket,
                currency=currency,
                defaults={
                    'amount': data['amount'],
                    'invoices_count': data['count'],
                    'computed_at': now,
                },
            )
            rows_written += 1

    return {'rows_written': rows_written, 'as_of': str(today), 'scope': scope}


METERING_OPEN = 'OPEN'                  # current month: the count is still being refreshed
METERING_FROZEN = 'FROZEN'              # month ended and a row exists: immutable (RPT-008), the invoice basis
METERING_NOT_COMPUTED = 'NOT_COMPUTED'  # no row for this school and month


def get_metering_statement(foundation_id, month, school_ids=None, today=None) -> dict:
    """RPT-009: the counted students per school for one month, as the invoice basis would read them.

    Reads `RptActiveStudent` only; it never recomputes (the count's single definition stays in
    `refresh_active_students`, RPT-007). `school_ids=None` means every school of the foundation;
    otherwise only those ids, so a school-scoped caller never sees another school's count.
    A school with no row for the month is listed as NOT_COMPUTED rather than as zero, because a
    missing count and a count of zero are different facts on an invoice dispute. `complete` says
    whether every listed school has a count, so `total_active` is never mistaken for the whole.

    RPT-010/RPT-011: each school also carries the month's money, all read from `rpt_*` tables.
    `subscription` is the frozen (or, while the month is open, running) `RptSubscriptionCharge` lines and
    their total; it is None until the school has a count, and empty `lines` mean no price list applies
    (a total of 0.00 then means "not priced", not "free"). `campus_take_rate` is the wallet commission
    (WAL-021, stored on each sale at its own rate) and `payment_fees` the fees recorded on settled
    payments: the gateway's fee at settlement, NOT EduCore's margin over it, which nothing records. Both
    are one entry per currency. `revenue_totals` sums the schools listed, per currency.
    """
    today = today or timezone.now().date()
    month = month.replace(day=1)
    schools = School.all_tenants.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('name', 'id')
    if school_ids is not None:
        schools = schools.filter(id__in=school_ids)
    counts = {
        row.school_id: row for row in RptActiveStudent.all_tenants.filter(
            foundation_id=foundation_id, month=month, deleted_at__isnull=True,
        )
    }
    with_roster = set(RptActiveStudentRoster.all_tenants.filter(
        foundation_id=foundation_id, month=month, deleted_at__isnull=True,
    ).values_list('school_id', flat=True))
    closed = _month_is_closed(month, today)
    last_day, _days = _month_days(month)
    school_list = list(schools)
    ids = [school.id for school in school_list]
    charges: dict[int, list] = {}
    for charge in RptSubscriptionCharge.all_tenants.filter(
        foundation_id=foundation_id, month=month, school_id__in=ids, deleted_at__isnull=True,
    ).order_by('module_key'):
        charges.setdefault(charge.school_id, []).append(charge)
    take_rate = _month_sums(RptWalletActivity, 'commission', foundation_id, ids, month, last_day)
    payment_fees = _month_sums(RptDailyFinance, 'fees', foundation_id, ids, month, last_day)
    totals: dict[str, dict] = {}

    def add_total(currency, key, amount):
        line = totals.setdefault(currency, {
            'currency': currency, 'subscription': Decimal('0.00'), 'campus_take_rate': Decimal('0.00'),
            'payment_fees': Decimal('0.00'),
        })
        line[key] += amount

    entries = []
    for school in school_list:
        row = counts.get(school.id)
        if row is None:
            state, active_count, computed_at = METERING_NOT_COMPUTED, None, None
        else:
            state = METERING_FROZEN if closed else METERING_OPEN
            active_count, computed_at = row.active_count, row.computed_at
        entries.append({
            'school_id': school.id, 'school_name': school.name, 'is_active': school.is_active,
            'state': state, 'active_count': active_count, 'computed_at': computed_at,
            # A roster exists only for counts made after rosters were captured; older frozen months have none.
            'roster_available': school.id in with_roster,
            'subscription': _subscription_block(charges.get(school.id, []), add_total) if row is not None else None,
            'campus_take_rate': _money_list(take_rate.get(school.id, {}), 'campus_take_rate', add_total),
            'payment_fees': _money_list(payment_fees.get(school.id, {}), 'payment_fees', add_total),
        })
    counted = [e['active_count'] for e in entries if e['active_count'] is not None]
    return {
        'month': month.strftime('%Y-%m'),
        'schools': entries,
        'total_active': sum(counted),
        'complete': len(counted) == len(entries),
        'revenue_totals': [
            {'currency': t['currency'], **{k: _amount(v) for k, v in t.items() if k != 'currency'}}
            for t in sorted(totals.values(), key=lambda t: t['currency'])
        ],
    }


def _amount(value: Decimal) -> str:
    """Money leaves the API as a string, never a float (CUR-026); the column's own scale is kept."""
    return format(value.quantize(Decimal('0.01')), 'f')


def _month_sums(model, field, foundation_id, school_ids, month, last_day) -> dict[int, dict[str, Decimal]]:
    """school id -> currency -> the sum of a daily rollup's money column over the month."""
    sums: dict[int, dict[str, Decimal]] = {}
    for school_id, currency, total in model.all_tenants.filter(
        foundation_id=foundation_id, school_id__in=school_ids, date__gte=month, date__lte=last_day,
        deleted_at__isnull=True,
    ).values_list('school_id', 'currency').annotate(total=Sum(field)):
        sums.setdefault(school_id, {})[currency] = total or Decimal('0.00')
    return sums


def _money_list(by_currency, total_key, add_total) -> list[dict]:
    for currency, amount in by_currency.items():
        add_total(currency, total_key, amount)
    return [{'amount': _amount(amount), 'currency': currency} for currency, amount in sorted(by_currency.items())]


def _subscription_block(charges, add_total) -> dict:
    lines = [
        {
            'module_key': c.module_key, 'plan_tier': c.plan_tier, 'unit_price': _amount(c.unit_price),
            'active_count': c.active_count, 'active_days': c.active_days, 'days_in_month': c.days_in_month,
            'amount': _amount(c.amount), 'currency': c.currency,
        }
        for c in charges
    ]
    by_currency: dict[str, Decimal] = {}
    for c in charges:
        by_currency[c.currency] = by_currency.get(c.currency, Decimal('0.00')) + c.amount
    for currency, amount in by_currency.items():
        add_total(currency, 'subscription', amount)
    return {'lines': lines, 'totals': [{'amount': _amount(a), 'currency': c} for c, a in sorted(by_currency.items())]}


def get_metering_roster(foundation_id, school, month, today=None) -> dict:
    """RPT-009: WHICH students one school's count for a month was made of, so an invoice dispute can be settled.

    `school` must already be checked as belonging to the foundation and to the caller's ceiling. Reads the
    roster captured with the count; it never recomputes one, because the past cannot be reconstructed. When
    there is no roster (count frozen before rosters existed, or not computed yet) `roster_available` is
    False and `students` is empty, and `state` says which, so an empty list is never read as "zero students".
    Each student carries their CURRENT status: someone counted in September who has since left shows as such.
    NIS is included so two students with the same name can be told apart; NISN is deliberately left out.
    """
    from apps.identity.models import Student

    today = today or timezone.now().date()
    month = month.replace(day=1)
    count = RptActiveStudent.all_tenants.filter(
        foundation_id=foundation_id, school=school, month=month, deleted_at__isnull=True,
    ).first()
    roster = RptActiveStudentRoster.all_tenants.filter(
        foundation_id=foundation_id, school=school, month=month, deleted_at__isnull=True,
    ).first()

    if count is None:
        state = METERING_NOT_COMPUTED
    else:
        state = METERING_FROZEN if _month_is_closed(month, today) else METERING_OPEN

    students = []
    if roster is not None:
        rows = Student.all_tenants.filter(
            foundation_id=foundation_id, id__in=roster.student_ids,
        ).select_related('person').order_by('person__full_name', 'id')
        students = [
            {'student_id': s.id, 'full_name': s.person.full_name, 'nis': s.nis, 'current_status': s.status}
            for s in rows
        ]
    return {
        'month': month.strftime('%Y-%m'),
        'school_id': school.id,
        'school_name': school.name,
        'state': state,
        'active_count': count.active_count if count else None,
        'roster_available': roster is not None,
        'captured_at': roster.captured_at if roster else None,
        'students': students,
    }

from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.fields import soft_delete_uniqueness_marker
from apps.core.models import TenantModel
from apps.identity.models import School


class RptWalletActivity(TenantModel):
    """Daily wallet activity rollup per school (spec/15 §2).

    Rebuilt by `refresh_reporting` (never written to directly by transactional code) —
    reports MUST read from here, never from WalletTransaction/POSTransaction directly
    (spec/15 §2: "All report queries MUST hit the rpt_* tables").
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='wallet_activity_reports')
    date = models.DateField()
    currency = models.CharField(max_length=3, default='IDR')
    topups = MoneyField(default=Decimal('0.00'))
    purchases = MoneyField(default=Decimal('0.00'))
    commission = MoneyField(default=Decimal('0.00'))
    active_wallets = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_wallet_activity'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'currency', 'active_uniq_marker'],
                name='unique_rpt_wallet_activity_per_school_day',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.date} ({self.currency})"


class RptDailyAttendance(TenantModel):
    """Daily attendance rollup per class (spec/15 §2). Feeds the "Daily attendance
    summary" report (spec/15 §3). Rebuilt by `refresh_reporting`.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='daily_attendance_reports')
    date = models.DateField()
    class_group = models.ForeignKey('academic.ClassGroup', on_delete=models.PROTECT, related_name='attendance_reports')
    present = models.PositiveIntegerField(default=0)
    late = models.PositiveIntegerField(default=0)
    sick = models.PositiveIntegerField(default=0)
    permitted = models.PositiveIntegerField(default=0)
    absent = models.PositiveIntegerField(default=0)
    rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_daily_attendance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'class_group', 'active_uniq_marker'],
                name='unique_rpt_daily_attendance_per_school_class_day',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.class_group.name} - {self.date} ({self.rate_pct}%)"


class RptAcademicPerformance(TenantModel):
    """Term-scoped academic performance rollup per class/subject (spec/15 §2). Feeds
    the "Grade distribution" report (spec/15 §3). Rebuilt by `refresh_reporting`.

    No `date` dimension — unlike the day-bucketed rpt_* tables, grades are scoped to
    a TERM, not a calendar day.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='academic_performance_reports')
    term = models.ForeignKey('academic.Term', on_delete=models.PROTECT, related_name='performance_reports')
    class_group = models.ForeignKey('academic.ClassGroup', on_delete=models.PROTECT, related_name='performance_reports')
    subject = models.ForeignKey('academic.Subject', on_delete=models.PROTECT, related_name='performance_reports')
    avg_score = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0.00'))
    band_distribution = models.JSONField(default=dict, help_text=_("{descriptor: count}"))
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_academic_performance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'term_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'term', 'class_group', 'subject', 'active_uniq_marker'],
                name='unique_rpt_academic_performance_per_school_term_class_subject',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.class_group.name} - {self.subject.name} ({self.term.name}): avg {self.avg_score}"


class RptActiveStudent(TenantModel):
    """Monthly active-student count per school (spec/15 §2, §4) — the invoice basis
    for EduCore's own subscription billing (RPT-007). RPT-008: once a month has
    fully ended, its row is immutable — `refresh_reporting` never recomputes it again.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='active_student_reports')
    month = models.DateField(help_text=_("Normalized to the 1st of the month"))
    active_count = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_active_students'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'month', 'active_uniq_marker'],
                name='unique_rpt_active_students_per_school_month',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.month:%Y-%m}: {self.active_count} active"


class RptActiveStudentRoster(TenantModel):
    """WHICH students one `RptActiveStudent` count was made of (RPT-009), so an invoice dispute can be settled.

    Written by `refresh_active_students` in the same transaction as the count and frozen with it (RPT-008).
    Past months cannot be reconstructed (the repo keeps no historical status or enrolment), so a month
    whose count was frozen before this table existed simply has no roster; readers must say so rather
    than show an empty list. Holds student ids only: names and NIS are joined in at read time, so this
    table carries no PII of its own.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='active_student_rosters')
    month = models.DateField(help_text=_("Normalized to the 1st of the month"))
    student_ids = models.JSONField(default=list, help_text=_("Ids of the students counted, in ascending order"))
    captured_at = models.DateTimeField(help_text=_("When the roster was taken (same instant as the count)"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_active_student_rosters'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'month', 'active_uniq_marker'],
                name='unique_rpt_active_student_roster_per_school_month',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.month:%Y-%m}: {len(self.student_ids)} students"


class RptSubscriptionCharge(TenantModel):
    """One module's subscription charge for one school and month (RPT-010): the counted students
    x the module's price for the foundation's tier, prorated by the days the module was entitled.

    Everything that produced `amount` is stored on the row, because none of it can be rebuilt later:
    the count is frozen (RPT-008), `plan_tier` is only the foundation's current label, and a price
    list can grow. It is frozen like the count: `refresh_subscription_charges` rewrites the row
    until the first run after the month has ended, and never again. A module with no price for the
    month has no row (it is not charged), so a school with no rows has no price list that applies.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='subscription_charges')
    month = models.DateField(help_text=_("Normalized to the 1st of the month"))
    module_key = models.CharField(max_length=32)
    plan_tier = models.CharField(max_length=32, help_text="The foundation's tier when the row was computed")
    currency = models.CharField(max_length=3, default='IDR')
    active_count = models.PositiveIntegerField(help_text="The month's `RptActiveStudent` count")
    unit_price = MoneyField(help_text="Per student per month, from `ModulePrice`")
    active_days = models.PositiveSmallIntegerField(help_text="Days of the month the module was entitled")
    days_in_month = models.PositiveSmallIntegerField()
    amount = MoneyField(help_text="active_count x unit_price x active_days / days_in_month, rounded half-up once")
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_subscription_charges'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'month', 'module_key', 'active_uniq_marker'],
                name='unique_rpt_subscription_charge_per_school_month_module',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.month:%Y-%m} {self.module_key}: {self.currency} {self.amount}"


class RptDailyFinance(TenantModel):
    """Daily finance rollup per school (spec/15 §2). Feeds the "Collection
    performance" report (spec/15 §3). Rebuilt by `refresh_reporting`.

    `outstanding` is a live snapshot, not a per-day delta like the others — this
    schema has no historical point-in-time balance_due ledger, so it carries the
    CURRENT total outstanding balance at the time of the refresh, attached
    identically to every day-row written in that run (documented limitation, same
    spirit as every other rollup's "accurate as of computation time" simplification).
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='daily_finance_reports')
    date = models.DateField()
    currency = models.CharField(max_length=3, default='IDR')
    billed = MoneyField(default=Decimal('0.00'))
    collected = MoneyField(default=Decimal('0.00'))
    outstanding = MoneyField(default=Decimal('0.00'))
    payments_count = models.PositiveIntegerField(default=0)
    fees = MoneyField(default=Decimal('0.00'))
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_daily_finance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'currency', 'active_uniq_marker'],
                name='unique_rpt_daily_finance_per_school_day',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.date} ({self.currency})"


class RptArAging(TenantModel):
    """AR Aging rollup per student per bucket (spec/15 §2, FIN-029).
    Feeds the "AR aging" and "Arrears by student" reports (spec/15 §3).
    Rebuilt by `refresh_reporting`.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='ar_aging_reports')
    student = models.ForeignKey('identity.Student', on_delete=models.PROTECT, related_name='ar_aging_reports')
    as_of = models.DateField()
    bucket = models.CharField(max_length=16, help_text=_("CURRENT, 0_30, 31_60, 61_90, 90_PLUS"))
    currency = models.CharField(max_length=3, default='IDR')
    amount = MoneyField(default=Decimal('0.00'))
    invoices_count = models.PositiveIntegerField(default=1)
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'rpt_ar_aging'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'as_of']),
            models.Index(fields=['foundation_id', 'student_id', 'as_of']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'student', 'as_of', 'bucket', 'currency', 'active_uniq_marker'],
                name='unique_rpt_ar_aging_per_student_as_of_bucket',
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.bucket} ({self.amount} {self.currency})"


class RptParentWeeklyActivity(TenantModel):
    """Weekly health rollup per school: parent-app activity (RPT-012 north star: weekly active parent accounts /
    enrolled students) plus the other RPT-013 metrics (see the RPT-013 columns below).

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

    # RPT-013 health metrics beside the parent WAU, one row per school per week. Each is a numerator and
    # a denominator so the ratio can be re-derived; null until the week's health part was computed.
    collection_billed = MoneyField(null=True, blank=True, help_text=_("IDR invoices due in the week (issued, part-paid, paid, written off)"))
    collection_collected = MoneyField(null=True, blank=True, help_text=_("Of those, settled by the end of the week"))
    attendance_expected_periods = models.PositiveIntegerField(null=True, blank=True, help_text=_("Timetable periods that fell in the week and needed attendance"))
    attendance_submitted_periods = models.PositiveIntegerField(null=True, blank=True, help_text=_("Of those, periods with attendance submitted"))
    gate_samples = models.PositiveIntegerField(null=True, blank=True, help_text=_("Gate/face device availability samples in operational hours"))
    gate_up_samples = models.PositiveIntegerField(null=True, blank=True, help_text=_("Of those, samples where the device was reachable"))
    canteen_active_students = models.PositiveIntegerField(null=True, blank=True, help_text=_("Counted students with a completed canteen purchase in the week"))
    health_computed_at = models.DateTimeField(null=True, blank=True, help_text=_("When the RPT-013 columns were computed; a week computed after it ended is frozen"))
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

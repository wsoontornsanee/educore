from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
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

    class Meta:
        db_table = 'rpt_wallet_activity'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'currency'],
                condition=models.Q(deleted_at__isnull=True),
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

    class Meta:
        db_table = 'rpt_daily_attendance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'class_group'],
                condition=models.Q(deleted_at__isnull=True),
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

    class Meta:
        db_table = 'rpt_academic_performance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'term_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'term', 'class_group', 'subject'],
                condition=models.Q(deleted_at__isnull=True),
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

    class Meta:
        db_table = 'rpt_active_students'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'month'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_rpt_active_students_per_school_month',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.month:%Y-%m}: {self.active_count} active"


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

    class Meta:
        db_table = 'rpt_daily_finance'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'currency'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_rpt_daily_finance_per_school_day',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.date} ({self.currency})"

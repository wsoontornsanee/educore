"""Foundation portal models and read-model rollups (spec/03 §4)."""
from decimal import Decimal
from django.db import models
from apps.core.fields import MoneyField, soft_delete_uniqueness_marker
from apps.core.models import TenantModel


class RptFoundationKPI(models.Model):
    """Reporting rollup table for Foundation Dashboard KPIs (spec/03 §4, spec/15).
    
    Rebuilt periodically by `refresh_reporting` cron task.
    Reads from this table power the dashboard in <2s without transactional joins (FND-005).
    """
    id = models.BigAutoField(primary_key=True)
    foundation_id = models.BigIntegerField(db_index=True)
    school_id = models.BigIntegerField(null=True, blank=True, db_index=True, help_text="Null for foundation-wide aggregate")
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)

    # Monetary metrics (CUR-005: MoneyField DECIMAL(18,2))
    billed = MoneyField(default=Decimal('0.00'), help_text="Total amount billed in period")
    collected = MoneyField(default=Decimal('0.00'), help_text="Total amount collected in period")
    outstanding = MoneyField(default=Decimal('0.00'), help_text="Total outstanding AR")
    ar_0_30 = MoneyField(default=Decimal('0.00'), help_text="AR aging bucket 0-30 days")
    ar_31_60 = MoneyField(default=Decimal('0.00'), help_text="AR aging bucket 31-60 days")
    ar_61_90 = MoneyField(default=Decimal('0.00'), help_text="AR aging bucket 61-90 days")
    ar_90_plus = MoneyField(default=Decimal('0.00'), help_text="AR aging bucket 90+ days")
    campus_spend = MoneyField(default=Decimal('0.00'), help_text="Cashless canteen/store spend")

    # Operational metrics
    active_students = models.PositiveIntegerField(default=0)
    avg_attendance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))

    # Multi-currency metadata (CUR-007, FND-005b)
    currency = models.CharField(max_length=3, default='IDR')
    reporting_currency = models.CharField(max_length=3, default='IDR')
    fx_rate_date = models.DateField(null=True, blank=True)

    # Multi-currency consolidation status (FND-005b)
    MULTI_CURRENCY_SINGLE = 'SINGLE_CURRENCY'
    MULTI_CURRENCY_CONSOLIDATED = 'CONSOLIDATED'
    MULTI_CURRENCY_UNSUPPORTED = 'MIXED_CURRENCY_UNSUPPORTED'
    MULTI_CURRENCY_CHOICES = [
        (MULTI_CURRENCY_SINGLE, 'Single Currency'),
        (MULTI_CURRENCY_CONSOLIDATED, 'Consolidated'),
        (MULTI_CURRENCY_UNSUPPORTED, 'Mixed Currency Unsupported'),
    ]
    multi_currency_status = models.CharField(
        max_length=32, choices=MULTI_CURRENCY_CHOICES, default=MULTI_CURRENCY_SINGLE,
        help_text="Consolidation status for multi-currency foundations (FND-005b)",
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rpt_foundation_kpis'
        verbose_name = 'Rollup KPI Yayasan'
        verbose_name_plural = 'Daftar Rollup KPI Yayasan'
        indexes = [
            models.Index(fields=['foundation_id', 'period_start', 'period_end']),
            models.Index(fields=['foundation_id', 'school_id']),
        ]

    def __str__(self):
        scope = f"School {self.school_id}" if self.school_id else "Foundation-wide"
        return f"KPI [{scope}] {self.period_start} to {self.period_end}"

    @property
    def collection_rate_pct(self) -> Decimal:
        """Calculated collection rate percentage."""
        if self.billed > Decimal('0.00'):
            return (self.collected / self.billed * Decimal('100.00')).quantize(Decimal('0.01'))
        return Decimal('0.00')


class FxRate(TenantModel):
    """Per-foundation exchange rate for consolidated reporting (CUR-021).

    Used exclusively by refresh_foundation_kpis (FND-005b) to convert per-school
    figures into the foundation's reporting_currency. Transactional records are
    NEVER converted or rewritten (CUR-022, CUR-025).
    """
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_IMPORTED = 'IMPORTED'
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_IMPORTED, 'Imported'),
    ]

    base_currency = models.CharField(max_length=3, help_text="Source currency (e.g. USD)")
    quote_currency = models.CharField(max_length=3, help_text="Target currency (e.g. IDR)")
    rate = models.DecimalField(max_digits=18, decimal_places=8, help_text="Conversion rate: 1 base_currency = rate quote_currency (CUR-021)")
    effective_date = models.DateField(help_text="Date this rate is effective from")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'fx_rates'
        verbose_name = 'Nilai Tukar'
        verbose_name_plural = 'Daftar Nilai Tukar'
        ordering = ['-effective_date']
        indexes = [
            models.Index(fields=['foundation_id', 'base_currency', 'quote_currency', 'effective_date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'base_currency', 'quote_currency', 'effective_date', 'active_uniq_marker'],
                name='unique_fx_rate_per_foundation_currency_date',
            ),
        ]

    def __str__(self):
        return f"{self.base_currency}/{self.quote_currency} = {self.rate} ({self.effective_date})"
